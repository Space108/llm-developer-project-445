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

Guardrail: input до всего хода; output на ответ и карточку; недоступный
сервис → session.guard_state["ok"] = False (fail-closed для действий).
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable

from app import audit, cards, editor, guardrail_client, idempotency, llm, planner, workers
from app.sessions import pep_of
from core import config, db

Emit = Callable[[dict], Awaitable[None]]

_UNSAFE_REFUSAL = (
    "Не могу обработать этот запрос — он выглядит небезопасно. "
    "Переформулируйте, пожалуйста, или позовите оператора."
)

_POLICY_DENY_TEXT = {
    "session_limit": "Лимит смен тарифа за эту сессию исчерпан — новые смены сейчас недоступны",
    "confirmation_expired": "Подтверждение устарело — повторите запрос на изменение",
    "card_not_prepared": "Нет подготовленной операции для подтверждения",
    "confirmation_missing": "Сначала подтвердите карточку",
    "confirmation_mismatch": "Подтверждение не совпадает с карточкой — повторите запрос",
    "pdp_unavailable": "Сервис решений недоступен — действие сейчас нельзя выполнить",
    "no_write_scope": "Изменение тарифа вам недоступно",
    "role_has_no_write": "Изменение тарифа вам недоступно",
    "guard_not_ok": "не работает проверка безопасности, могу позвать оператора",
    "deny_by_default": "Операция отклонена политикой",
}


def _policy_deny_text(reason: str) -> str:
    return _POLICY_DENY_TEXT.get(reason, f"Операция отклонена: {reason or 'deny'}")


def _audit_action(
    session,
    what: str,
    outcome: str,
    *,
    args=None,
    before=None,
    after=None,
    idempotency_key: str | None = None,
    detail: str | None = None,
) -> None:
    audit.append(
        session.user.sub,
        what,
        outcome,
        trace_id=session.trace_id or None,
        session_id=session.session_id,
        args=args,
        before=before,
        after=after,
        idempotency_key=idempotency_key,
        detail=detail,
    )


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
    """«Да» → PEP ConfirmCard + Execute → идемпотентное исполнение через action-MCP."""
    key = f"{card.action}:{card.pending_id}"
    row = db.fetchone("select state from pending_cards where pending_id = %s", (card.pending_id,))
    first_confirm = row is None or row["state"] == "pending"

    if first_confirm:
        pep = pep_of(session)
        confirmed = await pep.confirm(card)
        if not confirmed.allowed:
            state = "expired" if confirmed.reason == "confirmation_expired" else "denied"
            cards.settle(session, card, state)
            _audit_action(
                session,
                card.action,
                "denied",
                args={"pending_id": card.pending_id},
                detail=confirmed.reason,
            )
            return _policy_deny_text(confirmed.reason)

        executed = await pep.execute(card)
        if not executed.allowed:
            state = "expired" if executed.reason == "confirmation_expired" else "denied"
            cards.settle(session, card, state)
            _audit_action(
                session,
                card.action,
                "denied" if executed.reason != "session_limit" else "server_limit",
                args={"pending_id": card.pending_id},
                detail=executed.reason,
            )
            return _policy_deny_text(executed.reason)

        cards.settle(session, card, "confirmed")
    else:
        session.pending_card = None

    async def _call() -> dict:
        return await session.pool.call(
            "actions",
            "execute_action",
            {
                "action": card.action,
                "details_json": json.dumps(card.details, ensure_ascii=False),
                "pending_id": card.pending_id,
                "idempotency_key": key,
            },
        )

    stored = await idempotency.execute(key, session.user.sub, card.action, _call)
    result = stored.result or {}
    outcome = result.get("outcome")
    view_before = (card.view or {}).get("с")
    view_after = (card.view or {}).get("на")
    before = result.get("before", view_before)
    after = result.get("after", view_after)

    if stored.duplicate or outcome == "duplicate":
        _audit_action(
            session,
            card.action,
            "duplicate_suppressed",
            args=card.details,
            before=before,
            after=after,
            idempotency_key=key,
        )
    elif outcome == "executed" or outcome in {"toggled", "updated"}:
        _audit_action(
            session,
            card.action,
            "executed",
            args=card.details,
            before=before,
            after=after,
            idempotency_key=key,
        )
    elif outcome == "daily_limit":
        _audit_action(
            session,
            card.action,
            "server_limit",
            args=card.details,
            idempotency_key=key,
            detail="daily_limit",
        )
    else:
        _audit_action(
            session,
            card.action,
            "error",
            args=card.details,
            idempotency_key=key,
            detail=str(outcome or result.get("error") or result)[:200],
        )
    return _human_outcome(card.action, stored)


async def _escalate(session, card: cards.Card | None, message: str) -> str:
    if card is not None:
        cards.settle(session, card, "rejected")
    try:
        await session.pool.call(
            "actions",
            "create_ticket",
            {
                "subject": "эскалация из чата",
                "note": message[:400],
            },
        )
        _audit_action(
            session,
            "create_ticket" if card is None else card.action,
            "escalated",
            args={"pending_id": getattr(card, "pending_id", None)},
            detail="operator",
        )
    except Exception as e:  # noqa: BLE001
        _audit_action(session, "create_ticket", "error", detail=str(e)[:160])
        return "Не удалось создать обращение — попробуйте позже"
    return "Передаю обращение оператору поддержки"


async def _apply_guardrail(session, text: str, mode: str) -> guardrail_client.Verdict:
    """Пишет guard_state и аудит. available=False → ok=False; unsafe → ok=False."""
    verdict = await guardrail_client.check(text, mode=mode)
    if not verdict.available:
        session.guard_state["ok"] = False
        _audit_action(
            session,
            "guardrail",
            "guardrail_degraded",
            detail=mode,
        )
        return verdict
    if not verdict.safe:
        session.guard_state["ok"] = False
        _audit_action(
            session,
            "guardrail",
            "guardrail_unsafe",
            detail=verdict.category or mode,
            args={"mode": mode, "category": verdict.category},
        )
        return verdict
    session.guard_state["ok"] = True
    return verdict


async def run_turn(session, message: str, emit: Emit) -> None:
    session.trace_id = f"tr_{uuid.uuid4().hex[:12]}"

    # input-проверка до всего хода (в т.ч. до LLM и до «да» по карточке)
    inbound = await _apply_guardrail(session, message, "input")
    if inbound.available and not inbound.safe:
        await emit({"type": "reply", "text": _UNSAFE_REFUSAL, "sources": []})
        return

    live = cards.active(session)
    kind = cards.classify_answer(message)
    if live is not None:
        if kind == "yes":
            text = await execute_pending(session, live)
            await emit({"type": "reply", "text": text, "sources": []})
            return
        if kind == "no":
            cards.settle(session, live, "rejected")
            _audit_action(
                session,
                live.action,
                "rejected_by_user",
                args={"pending_id": live.pending_id},
            )
            await emit({"type": "reply", "text": "Отменяю", "sources": []})
            return
        if kind == "operator":
            text = await _escalate(session, live, message)
            await emit({"type": "reply", "text": text, "sources": []})
            return
        # topic — обычный ход, карточка остаётся живой
    elif kind == "yes":
        # повторное «да» после исполнения → идемпотентный ответ эталона
        prev = cards.last_confirmed(session)
        if prev is not None:
            text = await execute_pending(session, prev)
            await emit({"type": "reply", "text": text, "sources": []})
            return
        # «да» без карточки — фантом, без исполнения
        _audit_action(session, "confirm", "denied", detail="card_not_prepared")
        await emit(
            {
                "type": "reply",
                "text": _policy_deny_text("card_not_prepared"),
                "sources": [],
            }
        )
        return

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
        view = fresh.public_view(config.CARD_TTL_SEC)
        outbound = await _apply_guardrail(
            session, json.dumps(view, ensure_ascii=False), "output"
        )
        if outbound.available and not outbound.safe:
            cards.settle(session, fresh, "denied")
            await emit({"type": "reply", "text": _UNSAFE_REFUSAL, "sources": []})
            return
        await emit(
            {
                "type": "card",
                "pending_id": fresh.pending_id,
                "card": view,
            }
        )
        return

    ed = editor.build_editor(session)
    edited = await llm.run_agent(
        ed, editor.editor_task(message, runs), trace_id=session.trace_id
    )
    reply_text = edited["report"]
    outbound = await _apply_guardrail(session, reply_text, "output")
    if outbound.available and not outbound.safe:
        live_out = cards.active(session)
        if live_out is not None:
            cards.settle(session, live_out, "denied")
        reply_text = _UNSAFE_REFUSAL
        sources: list[str] = []
    else:
        sources = _sources_from_runs(runs)
    await emit({"type": "reply", "text": reply_text, "sources": sources})
