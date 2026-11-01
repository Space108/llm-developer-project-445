"""Обёртки MCP-инструментов для агентов: вызов с JWT + учёт трейса.

Готовая основа: MCP-инструмент превращается в FunctionTool агента;
субъект вызова в Bearer-токене соединения; кривой JSON аргументов —
машинно-читаемый ответ модели, сессия не роняется; ошибка MCP-сервера
(MCPError, код + data с подсказками) — JSON tool-результат для модели.

Два барьера: visible_tools при сборке; admit перед каждым вызовом.
"""
from __future__ import annotations

import copy
import json
import re
import time

from agents import FunctionTool
from agents.run_context import RunContextWrapper
from mcp.shared.exceptions import MCPError

from app import audit, cards, permissions
from app.sessions import Session, pep_of
from core import config, db

_PREPARE_TOOLS = frozenset(
    {
        "prepare_tariff_change",
        "prepare_toggle_service",
        "prepare_contacts_update",
    }
)
_ARGS_LIMIT = 800

# Имена из каталога стенда — модель иногда подменяет «Семейный» на «Игровой».
_TARIFF_NAMES = ("Семейный", "Игровой", "Базовый", "Про")
_SERVICE_NAMES = ("Музыка", "Роуминг", "Антивирус")
_SUB_ID_RE = re.compile(r"\bu_[a-z0-9]+\b", re.IGNORECASE)


def _ground_prepare_args(session: Session, name: str, args: dict) -> dict:
    """Если в реплике абонента есть точное имя/id — подставляем в аргументы инструмента."""
    raw_msg = getattr(session, "last_user_message", "") or ""
    msg = raw_msg.lower()
    if not msg:
        return args
    out = dict(args)
    if name == "prepare_tariff_change":
        for tariff in _TARIFF_NAMES:
            if tariff.lower() in msg:
                out["tariff"] = tariff
                break
    elif name == "prepare_toggle_service":
        for service in _SERVICE_NAMES:
            if service.lower() in msg:
                out["service"] = service
                break
        if "выключ" in msg or "отключ" in msg:
            out["enabled"] = False
        elif "включи" in msg or "подключ" in msg:
            out["enabled"] = True
    elif name == "get_subscriber_overview":
        # Qwen часто шлёт foreign_id / placeholder вместо u_4f2a из реплики.
        match = _SUB_ID_RE.search(raw_msg)
        if match:
            out["subscriber_id"] = match.group(0)
    return out


def _schema_without_sub(schema: dict) -> dict:
    cleaned = copy.deepcopy(schema) if schema else {"type": "object", "properties": {}}
    props = cleaned.get("properties") or {}
    props.pop("sub", None)
    cleaned["properties"] = props
    required = cleaned.get("required")
    if isinstance(required, list):
        cleaned["required"] = [r for r in required if r != "sub"]
    return cleaned


def _trim_args(args: dict) -> dict:
    raw = json.dumps(args, ensure_ascii=False)
    if len(raw) <= _ARGS_LIMIT:
        return args
    return {"_truncated": raw[:_ARGS_LIMIT]}


def _log_tool(session: Session, tool: str, args: dict, outcome: str, started: float) -> None:
    db.execute(
        "insert into tool_calls(trace_id, tool, args, outcome, latency_ms)"
        " values (%s, %s, %s::jsonb, %s, %s)",
        (
            session.trace_id or None,
            tool,
            json.dumps(_trim_args(args), ensure_ascii=False),
            outcome,
            int((time.perf_counter() - started) * 1000),
        ),
    )


def wrap_mcp_tool(session: Session, name: str) -> FunctionTool:
    """Обёртка одного MCP-инструмента для агента этой сессии."""

    async def invoke(ctx: RunContextWrapper, input_json: str) -> str:
        del ctx
        started = time.perf_counter()
        try:
            args = json.loads(input_json) if input_json and input_json.strip() else {}
        except (TypeError, ValueError):
            _log_tool(session, name, {"raw": (input_json or "")[:200]}, "bad_arguments", started)
            return json.dumps(
                {"error": "bad_arguments", "detail": "аргументы — не JSON; повтори вызов по схеме"},
                ensure_ascii=False,
            )

        allowlist = session.allowlist
        if allowlist is None:
            allowlist = permissions.allowlist_from_claims(session.claims or {})
        decision = permissions.admit(allowlist, name, args)
        if not decision.allowed:
            audit.append(
                who=session.user.sub,
                what=name,
                outcome="denied",
                trace_id=session.trace_id or None,
                session_id=session.session_id,
                args=args,
                detail=decision.reason,
            )
            _log_tool(session, name, args, decision.reason[:60], started)
            return json.dumps(
                {"error": "denied", "reason": decision.reason},
                ensure_ascii=False,
            )

        session.note(name)
        args = _ground_prepare_args(session, name, args)
        try:
            result = await session.pool.call(session.pool.resource_of(name), name, args)
        except MCPError as e:
            data = e.data if isinstance(e.data, dict) else {}
            outcome = str(getattr(e, "message", "tool_failed"))[:60]
            _log_tool(session, name, args, outcome, started)
            return json.dumps(
                {"error": outcome, **data},
                ensure_ascii=False,
            )
        except Exception as e:  # noqa: BLE001 — сбой сервера становится данными для модели
            _log_tool(session, name, args, "tool_failed", started)
            return json.dumps({"error": "tool_failed", "detail": str(e)[:160]}, ensure_ascii=False)

        if name in _PREPARE_TOOLS and isinstance(result, dict) and result.get("ok"):
            card = cards.create(
                session,
                result["action"],
                result.get("details") or {},
                result.get("view") or {},
            )
            decision = await pep_of(session).prepare(card)
            if not decision.allowed:
                cards.settle(session, card, "denied")
                audit.append(
                    who=session.user.sub,
                    what=name,
                    outcome="denied",
                    trace_id=session.trace_id or None,
                    session_id=session.session_id,
                    args=args,
                    detail=decision.reason,
                )
                _log_tool(session, name, args, decision.reason[:60] or "denied", started)
                return json.dumps(
                    {
                        "error": "denied",
                        "reason": decision.reason,
                        "detail": "подготовка отклонена политикой; карточку не показывай",
                    },
                    ensure_ascii=False,
                )
            _log_tool(session, name, args, "ok", started)
            return json.dumps(
                {
                    "card_created": True,
                    "card": card.public_view(ttl_sec=config.CARD_TTL_SEC),
                    "pending_id": card.pending_id,
                    "next": "перескажи карточку и спроси подтверждение «да/нет»",
                },
                ensure_ascii=False,
            )
        _log_tool(session, name, args, "ok", started)
        return json.dumps(result, ensure_ascii=False)

    return FunctionTool(
        name=name,
        description=session.pool.tool_description(name),
        params_json_schema=_schema_without_sub(session.pool.tool_schema(name)),
        on_invoke_tool=invoke,
    )


def wrap_tools(session: Session, names: list[str]) -> list[FunctionTool]:
    """Собрать обёртки: барьер 1 режет имена; execute_action модели не отдаём."""
    known = set(session.pool.tool_names())
    if session.allowlist is None:
        visible = [n for n in names if n in known and n != "execute_action"]
    else:
        visible = permissions.visible_tools(session.allowlist, list(names))
    return [wrap_mcp_tool(session, n) for n in visible if n in known]
