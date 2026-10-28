"""Оркестратор хода: план → гейт → доменные агенты → редактор → SSE.

Ход — один запрос абонента; оркестратор управляет запуском агентов и
собирает события хода для фронтенда.

TODO(шаг 1) — read-контур:
  run_turn(session, message, emit):
    1. session.trace_id = f"tr_{uuid.uuid4().hex[:12]}"
    2. план: planner.build_planner() лениво → planner.next_plan →
       planner.gate; отброшенные шаги — в session.trace;
       событие {"type": "plan", "agents": [...], "dropped": [...]}.
    3. plan.question без шагов → {"type": "reply", "text": question}.
    4. запуск агентов по шагам (llm.run_agent с trace_id); после агентов —
       редактор; событие {"type": "reply", "text", "sources"}
       (sources — источники kb из tool-результатов).
  Emit = Callable[[dict], Awaitable[None]].

TODO(шаг 2) — карточка действия:
  - в начале хода: cards.active(session) → cards.classify_answer(message):
    «да» → execute_pending, «нет» → settle("rejected") + «отменяю»,
    «оператор» → escalate, смена темы → обычный ход с живой карточкой;
  - после агентов: свежая карточка → событие {"type": "card"} и ход
    завершается (ждём подтверждение);
  - execute_pending(session, card): settle("confirmed"), ключ
    f"{action}:{pending_id}", idempotency.execute → вызов MCP execute_action
    → человекочитаемый ответ (executed / duplicate / daily_limit / отказ).

TODO(шаг 3) — аудит: audit.append на все исходы пути действия
  (executed с before/after, duplicate_suppressed, rejected_by_user,
  server_limit, escalated).

TODO(шаг 4) — права: «да» → PEP: pep.confirm → pep.execute; при Deny —
  человекочитаемый отказ по reason (session_limit, confirmation_expired,
  card_not_prepared, pdp_unavailable).

TODO(шаг 5) — guardrail: input-проверка реплики до всего хода (unsafe →
  отказ, запись в аудит); output-проверка ответа и карточки до показа;
  недоступный сервис → session.guard_state["ok"] = False (fail-closed
  для действий), чтение продолжается.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable

from app import cards, editor, idempotency, llm, planner, workers
from core import config

Emit = Callable[[dict], Awaitable[None]]


def _sources_from_runs(runs: list[dict]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(src: str) -> None:
        if src and src not in seen:
            seen.add(src)
            found.append(src)

    def walk(value) -> None:
        if isinstance(value, dict):
            src = value.get("source")
            if isinstance(src, str):
                add(src)
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str):
            text = value.strip()
            if text.startswith("{") or text.startswith("["):
                try:
                    walk(json.loads(text))
                except (TypeError, ValueError):
                    pass

    for run in runs:
        for meta in (run.get("tools") or {}).values():
            walk(meta.get("output"))
    return found


def _human_outcome(action: str, stored: idempotency.StoredResult) -> str:
    result = stored.result or {}
    outcome = result.get("outcome")
    if stored.duplicate or outcome == "duplicate":
        return "Эта операция уже исполнена — повтор не выполнялся"
    if outcome == "executed" or outcome == "toggled" or outcome == "updated":
        label = {
            "tariff_change": "смена тарифа",
            "service_toggle": "изменение услуги",
            "contacts_update": "обновление контактов",
        }.get(action, action)
        return f"Готово, операция «{label}» выполнена"
    if outcome == "daily_limit":
        return "Дневной лимит смен тарифа исчерпан — попробуйте завтра"
    if result.get("error"):
        return f"Не удалось выполнить: {result.get('detail') or result.get('error')}"
    return f"Исход: {outcome or result}"


async def execute_pending(session, card: cards.Card) -> str:
    """Подтверждённая карточка → идемпотентное исполнение через action-MCP."""
    cards.settle(session, card, "confirmed")
    key = f"{card.action}:{card.pending_id}"

    async def _call() -> dict:
        return await session.pool.call(
            "actions",
            "execute_action",
            {
                "sub": session.user.sub,
                "action": card.action,
                "details_json": json.dumps(card.details, ensure_ascii=False),
                "pending_id": card.pending_id,
                "idempotency_key": key,
            },
        )

    stored = await idempotency.execute(key, session.user.sub, card.action, _call)
    return _human_outcome(card.action, stored)


async def _escalate(session, card: cards.Card | None, message: str) -> str:
    if card is not None:
        cards.settle(session, card, "rejected")
    try:
        await session.pool.call(
            "actions",
            "create_ticket",
            {
                "sub": session.user.sub,
                "subject": "эскалация из чата",
                "note": message[:400],
            },
        )
    except Exception:  # noqa: BLE001
        return "Не удалось создать обращение — попробуйте позже"
    return "Передаю обращение оператору поддержки"


async def run_turn(session, message: str, emit: Emit) -> None:
    session.trace_id = f"tr_{uuid.uuid4().hex[:12]}"

    live = cards.active(session)
    if live is not None:
        kind = cards.classify_answer(message)
        if kind == "yes":
            text = await execute_pending(session, live)
            await emit({"type": "reply", "text": text, "sources": []})
            return
        if kind == "no":
            cards.settle(session, live, "rejected")
            await emit({"type": "reply", "text": "Отменяю", "sources": []})
            return
        if kind == "operator":
            text = await _escalate(session, live, message)
            await emit({"type": "reply", "text": text, "sources": []})
            return
        # topic — обычный ход, карточка остаётся живой

    if session.planner is None:
        session.planner = planner.build_planner()

    raw_plan, session.thread = await planner.next_plan(
        session.planner, session.thread, message
    )
    plan, dropped = planner.gate(raw_plan)
    for name in dropped:
        session.note(f"dropped:{name}")

    await emit(
        {
            "type": "plan",
            "agents": [s.agent for s in plan.steps],
            "dropped": dropped,
        }
    )

    if plan.question and not plan.steps:
        await emit({"type": "reply", "text": plan.question, "sources": []})
        return

    agent_ids = tuple(dict.fromkeys(s.agent for s in plan.steps))
    built = workers.build_workers(session, agent_ids)
    runs: list[dict] = []
    for step in plan.steps:
        agent = built.get(step.agent)
        if agent is None:
            session.trace.append(f"skip:{step.agent}")
            continue
        result = await llm.run_agent(agent, step.task, trace_id=session.trace_id)
        runs.append({"agent": step.agent, "task": step.task, **result})

    fresh = cards.active(session)
    if fresh is not None and (live is None or fresh.pending_id != getattr(live, "pending_id", None)):
        await emit(
            {
                "type": "card",
                "pending_id": fresh.pending_id,
                "card": fresh.public_view(config.CARD_TTL_SEC),
            }
        )
        return

    ed = editor.build_editor(session)
    edited = await llm.run_agent(
        ed, editor.editor_task(message, runs), trace_id=session.trace_id
    )
    await emit(
        {
            "type": "reply",
            "text": edited["report"],
            "sources": _sources_from_runs(runs),
        }
    )
