"""Сессии чата: identity, соединения с MCP, история планировщика.

Одна сессия = один пользователь чата. При открытии сессии backend —
доверенный клиент — выпускает у издателя токены для всех MCP-серверов
(по одному на resource, RFC 8707) и держит открытые соединения.

claims токена определяют allowlist (app.permissions). pep — клиент PDP
(лениво через pep_of). guard_state — verdikt guardrail (шаг после PDP).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app import permissions
from app.pep import Pep
from core import config, db
from core.mcpclient import McpPool
from core.tokens import Tokens


@dataclass
class Session:
    login: str
    user: config.User
    tokens: Tokens
    pool: McpPool
    claims: dict = field(default_factory=dict)
    allowlist: permissions.Allowlist | None = None
    pep: Pep | None = None
    guard_state: dict = field(default_factory=lambda: {"ok": True})
    thread: list = field(default_factory=list)   # история планировщика
    trace: list[str] = field(default_factory=list)
    trace_id: str = ""                           # сквозной id текущего хода
    planner: object | None = None                # собирается лениво на первом ходе
    pending_card: object | None = None           # живая карточка подтверждения
    session_id: str = field(default_factory=lambda: f"sess_{uuid.uuid4().hex[:8]}")

    def note(self, tool: str) -> None:
        self.trace.append(tool)


SESSIONS: dict[str, Session] = {}


async def open_session(login: str) -> Session:
    user = config.USERS.get(login)
    if user is None:
        raise KeyError(f"неизвестный пользователь: {login}")

    tokens = Tokens(login)
    pool = McpPool(tokens, resource_keys=("crm", "billing", "kb", "actions"))
    await pool.connect()

    claims = await tokens.claims("crm")
    allowlist = permissions.allowlist_from_claims(claims)
    session = Session(
        login=login,
        user=user,
        tokens=tokens,
        pool=pool,
        claims=claims,
        allowlist=allowlist,
    )
    db.execute(
        "insert into sessions(session_id, login, sub) values (%s, %s, %s)",
        (session.session_id, login, user.sub),
    )
    SESSIONS[session.session_id] = session
    return session


def get(session_id: str) -> Session | None:
    return SESSIONS.get(session_id)


def pep_of(session: Session) -> Pep:
    """Клиент PDP сессии: создаётся при первом обращении, дальше тот же."""
    if session.pep is None:
        session.pep = Pep(
            session_id=session.session_id,
            claims=session.claims,
            guard_state=session.guard_state,
        )
    return session.pep
