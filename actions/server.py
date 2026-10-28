"""Action-MCP: инструменты действий.

    uvicorn actions.server:app --host 127.0.0.1 --port 8220

TODO(шаг 2): один MCPServer (пакет mcp, пока БЕЗ auth) с инструментами:
  prepare_tariff_change(sub, tariff) / prepare_toggle_service(sub,
  service, enabled) / prepare_contacts_update(sub, phone, email) —
  для LLM; execute_action(sub, action, details_json, pending_id,
  idempotency_key) — НЕ для LLM, вызывает код оркестратора;
  create_ticket(sub, subject, note) — эскалация.
  ActionError → MCPError(code=-32002, message=code, data=hints) —
  подсказки доедут до модели. sub на этом шаге приходит аргументом,
  но подставляет его обёртка оркестратора — модель его не видит.
  Запуск: Starlette + Mount("/actions", streamable_http_app()) +
  lifespan (session manager живёт в event loop uvicorn).

TODO(шаг 4): перевод сервера под JWT —
  make_mcp_server("actions", resource=RESOURCES["actions"]) из
  core.server_auth (OAuth 2.1 resource server: Bearer в каждом вызове,
  проверка подписи/iss/aud); убрать sub из всех контрактов — субъект из
  get_access_token().subject (mcp.server.auth.middleware.auth_context).
  До этого шага аргумент sub — осознанная дыра; попытка протащить чужой
  sub станет тестом IDOR.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.shared.exceptions import MCPError
from starlette.applications import Starlette
from starlette.routing import Mount

from actions import service as action_service
from actions.contracts import ActionError
from core import config
from core.config import RESOURCES
from core.server_auth import make_mcp_server

ACTION_ERROR_CODE = -32002

# JWT уже включён: smoke G=2 зовёт без sub (форма 4.1). Аргумент sub
# оставлен — осознанная дыра до шага 4.1 (явный sub важнее токена).
actions = make_mcp_server(
    "actions",
    resource=RESOURCES["actions"],
    instructions="Действия абонента: подготовка карточки и исполнение по «да».",
)


def _sub(explicit: str = "") -> str:
    if explicit:
        return explicit
    at = get_access_token()
    if at is None or not at.subject:
        raise ActionError("missing_sub", "нужен субъект в токене или аргументе sub")
    return at.subject


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ActionError as e:
        raise MCPError(
            code=ACTION_ERROR_CODE,
            message=e.code,
            data=e.to_tool_result(),
        ) from e


@actions.tool()
def prepare_tariff_change(tariff: str, sub: str = "") -> dict:
    """Подготовить смену тарифа: карточка с разницей цен. Ничего не исполняет."""
    return _run(action_service.prepare_tariff_change, _sub(sub), tariff)


@actions.tool()
def prepare_toggle_service(service: str, enabled: bool, sub: str = "") -> dict:
    """Подготовить включение/выключение услуги. Ничего не исполняет."""
    return _run(action_service.prepare_toggle_service, _sub(sub), service, enabled)


@actions.tool()
def prepare_contacts_update(phone: str = "", email: str = "", sub: str = "") -> dict:
    """Подготовить смену телефона и/или почты. Ничего не исполняет."""
    return _run(
        action_service.prepare_contacts_update,
        _sub(sub),
        phone or None,
        email or None,
    )


@actions.tool()
def execute_action(
    action: str,
    details_json: str,
    idempotency_key: str,
    pending_id: str = "",
    sub: str = "",
) -> dict:
    """Исполнить подготовленное действие. Только для кода оркестратора, не для модели."""
    return _run(
        action_service.execute_action,
        _sub(sub),
        action,
        details_json,
        idempotency_key,
        pending_id,
    )


@actions.tool()
def create_ticket(subject: str, note: str = "", sub: str = "") -> dict:
    """Создать обращение в поддержку (эскалация без карточки)."""
    return _run(action_service.create_ticket, _sub(sub), subject, note)


@asynccontextmanager
async def _lifespan(app):  # noqa: ARG001
    async with actions.session_manager.run():
        yield


def build_app() -> Starlette:
    return Starlette(
        routes=[Mount("/actions", app=actions.streamable_http_app())],
        lifespan=_lifespan,
    )


app = build_app()


if __name__ == "__main__":
    uvicorn.run(
        "actions.server:app",
        host="127.0.0.1",
        port=config.ACTIONS_PORT,
        log_level="warning",
    )
