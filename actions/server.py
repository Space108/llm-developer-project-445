"""Action-MCP: инструменты действий.

    uvicorn actions.server:app --host 127.0.0.1 --port 8220

Сервер ресурса через make_mcp_server: Bearer в каждом вызове, субъект
из проверенного токена. Аргумента sub в контрактах нет.
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

actions = make_mcp_server(
    "actions",
    resource=RESOURCES["actions"],
    instructions="Действия абонента: подготовка карточки и исполнение по «да».",
)


def _subject() -> str:
    at = get_access_token()
    if at is None or not at.subject:
        raise ActionError("missing_sub", "нужен субъект в токене")
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
def prepare_tariff_change(tariff: str) -> dict:
    """Подготовить смену тарифа: карточка с разницей цен. Ничего не исполняет."""
    return _run(action_service.prepare_tariff_change, _subject(), tariff)


@actions.tool()
def prepare_toggle_service(service: str, enabled: bool) -> dict:
    """Подготовить включение/выключение услуги. Ничего не исполняет."""
    return _run(action_service.prepare_toggle_service, _subject(), service, enabled)


@actions.tool()
def prepare_contacts_update(phone: str = "", email: str = "") -> dict:
    """Подготовить смену телефона и/или почты. Ничего не исполняет."""
    return _run(
        action_service.prepare_contacts_update,
        _subject(),
        phone or None,
        email or None,
    )


@actions.tool()
def execute_action(
    action: str,
    details_json: str,
    idempotency_key: str,
    pending_id: str = "",
) -> dict:
    """Исполнить подготовленное действие. Только для кода оркестратора, не для модели."""
    return _run(
        action_service.execute_action,
        _subject(),
        action,
        details_json,
        idempotency_key,
        pending_id,
    )


@actions.tool()
def create_ticket(subject: str, note: str = "") -> dict:
    """Создать обращение в поддержку (эскалация без карточки)."""
    return _run(action_service.create_ticket, _subject(), subject, note)


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
