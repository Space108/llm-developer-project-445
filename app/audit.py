"""Аудит: append-only журнал в Postgres — основа разбора инцидентов.

TODO(шаг 3):
  1. OUTCOMES — фиксированная таксономия исходов: executed,
     duplicate_suppressed, rejected_by_user, rejected_by_contract, expired,
     escalated, denied, guardrail_unsafe, guardrail_degraded,
     token_refreshed, server_limit, budget_exhausted, error.
  2. append(who, what, outcome, *, trace_id=None, session_id=None, args=None,
     before=None, after=None, idempotency_key=None, detail=None) — INSERT в
     audit_log. Журнал не редактируется: только append и чтение.
  3. tail(n=20, who=None) и trace(trace_id) — чтение для разбора инцидентов.

В журнал попадают и неуспешные попытки: по нему видно «кто пытался и что».
Не логируйте текст реплик целиком — журнал про действия, не про переписку.
"""
from __future__ import annotations

import json
from typing import Any

from core import db

OUTCOMES = frozenset(
    {
        "executed",
        "duplicate_suppressed",
        "rejected_by_user",
        "rejected_by_contract",
        "expired",
        "escalated",
        "denied",
        "guardrail_unsafe",
        "guardrail_degraded",
        "token_refreshed",
        "server_limit",
        "budget_exhausted",
        "error",
    }
)


def _jsonb(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def append(
    who: str,
    what: str,
    outcome: str,
    *,
    trace_id: str | None = None,
    session_id: str | None = None,
    args: Any = None,
    before: Any = None,
    after: Any = None,
    idempotency_key: str | None = None,
    detail: str | None = None,
) -> None:
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown audit outcome: {outcome}")
    db.execute(
        "insert into audit_log(trace_id, session_id, who, what, outcome, args, before, after, idempotency_key, detail)"
        " values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)",
        (
            trace_id,
            session_id,
            who,
            what,
            outcome,
            _jsonb(args),
            _jsonb(before),
            _jsonb(after),
            idempotency_key,
            detail,
        ),
    )


def tail(n: int = 20, who: str | None = None) -> list[dict]:
    if who:
        return db.fetchall(
            "select * from audit_log where who = %s order by ts desc, id desc limit %s",
            (who, n),
        )
    return db.fetchall(
        "select * from audit_log order by ts desc, id desc limit %s",
        (n,),
    )


def trace(trace_id: str) -> dict:
    """Хронология одного хода: audit + llm_calls + tool_calls."""
    return {
        "trace_id": trace_id,
        "audit": db.fetchall(
            "select * from audit_log where trace_id = %s order by ts, id",
            (trace_id,),
        ),
        "llm_calls": db.fetchall(
            "select * from llm_calls where trace_id = %s order by ts, id",
            (trace_id,),
        ),
        "tool_calls": db.fetchall(
            "select * from tool_calls where trace_id = %s order by ts, id",
            (trace_id,),
        ),
    }
