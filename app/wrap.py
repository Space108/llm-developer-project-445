"""Обёртки MCP-инструментов для агентов: вызов с JWT + учёт трейса.

Готовая основа: MCP-инструмент превращается в FunctionTool агента;
субъект вызова в Bearer-токене соединения; кривой JSON аргументов —
машинно-читаемый ответ модели, сессия не роняется; ошибка MCP-сервера
(MCPError, код + data с подсказками) — JSON tool-результат для модели.

TODO(шаг 2): крючок prepare_* — успешный ответ ({"ok": true, "action",
  "details", "view"}) → cards.create(...) → вернуть модели
  {"card_created": true, "card", "next": "перескажи карточку и спроси
  подтверждение «да/нет»"}; пока параметр sub есть в схеме инструмента —
  подставляйте session.user.sub сами (модель его не видит).

TODO(шаг 3): каждый tool-вызов модели → таблицу tool_calls
  (trace_id, tool, args — обрезанные, outcome, latency_ms).

TODO(шаг 4): два барьера —
  wrap_tools: фильтровать имена через permissions.visible_tools
  (барьер 1: модель не видит запрещённого);
  invoke: permissions.admit(session.allowlist, name, args) перед вызовом
  (барьер 2: отказ → {"error": "denied", "reason"} + запись в аудит);
  подготовленную карточку проводить через pep.prepare — Deny → карточки
  нет, модель получает отказ с reason.
"""
from __future__ import annotations

import copy
import json

from agents import FunctionTool
from agents.run_context import RunContextWrapper
from mcp.shared.exceptions import MCPError

from app import cards
from app.sessions import Session
from core import config

_PREPARE_TOOLS = frozenset(
    {
        "prepare_tariff_change",
        "prepare_toggle_service",
        "prepare_contacts_update",
    }
)


def _schema_without_sub(schema: dict) -> dict:
    cleaned = copy.deepcopy(schema) if schema else {"type": "object", "properties": {}}
    props = cleaned.get("properties") or {}
    props.pop("sub", None)
    cleaned["properties"] = props
    required = cleaned.get("required")
    if isinstance(required, list):
        cleaned["required"] = [r for r in required if r != "sub"]
    return cleaned


def wrap_mcp_tool(session: Session, name: str) -> FunctionTool:
    """Обёртка одного MCP-инструмента для агента этой сессии."""

    async def invoke(ctx: RunContextWrapper, input_json: str) -> str:
        del ctx
        try:
            args = json.loads(input_json) if input_json and input_json.strip() else {}
        except (TypeError, ValueError):
            return json.dumps(
                {"error": "bad_arguments", "detail": "аргументы — не JSON; повтори вызов по схеме"},
                ensure_ascii=False,
            )
        if "sub" in session.pool.tool_schema(name).get("properties", {}):
            args["sub"] = session.user.sub
        session.note(name)
        try:
            result = await session.pool.call(session.pool.resource_of(name), name, args)
        except MCPError as e:
            data = e.data if isinstance(e.data, dict) else {}
            return json.dumps(
                {"error": getattr(e, "message", "tool_failed")[:60], **data},
                ensure_ascii=False,
            )
        except Exception as e:  # noqa: BLE001 — сбой сервера становится данными для модели
            return json.dumps({"error": "tool_failed", "detail": str(e)[:160]}, ensure_ascii=False)

        if name in _PREPARE_TOOLS and isinstance(result, dict) and result.get("ok"):
            card = cards.create(
                session,
                result["action"],
                result.get("details") or {},
                result.get("view") or {},
            )
            return json.dumps(
                {
                    "card_created": True,
                    "card": card.public_view(ttl_sec=config.CARD_TTL_SEC),
                    "pending_id": card.pending_id,
                    "next": "перескажи карточку и спроси подтверждение «да/нет»",
                },
                ensure_ascii=False,
            )
        return json.dumps(result, ensure_ascii=False)

    return FunctionTool(
        name=name,
        description=session.pool.tool_description(name),
        params_json_schema=_schema_without_sub(session.pool.tool_schema(name)),
        on_invoke_tool=invoke,
    )


def wrap_tools(session: Session, names: list[str]) -> list[FunctionTool]:
    """Собрать обёртки для перечня имён (какие имена давать — решает код)."""
    known = set(session.pool.tool_names())
    # execute_action модели не отдаём никогда
    return [wrap_mcp_tool(session, n) for n in names if n in known and n != "execute_action"]
