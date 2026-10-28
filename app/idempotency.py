"""Идемпотентность исполнения на стороне оркестратора.

TODO(шаг 2): StoredResult(duplicate, result) и execute(key, sub, action, fn):
  - ключ в таблице idempotency с результатом → StoredResult(duplicate=True,
    сохранённый результат) — повтор не исполняет операцию второй раз;
  - иначе исполнить fn(); неудачный исход (error в ответе) ключ не
    занимает — сбой не должен запирать повторную попытку;
  - успех → INSERT ... ON CONFLICT (key) DO NOTHING.

Ключ в оркестраторе — f"{action}:{pending_id}": одно «да» — одна операция.
Для смены тарифа дедупликация есть и на стороне биллинга — два барьера;
для услуг и контактов этот слой единственный.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from core import db

_FAIL_OUTCOMES = frozenset(
    {
        "not_found",
        "same_tariff",
        "unknown_tariff",
        "tariff_not_available",
        "daily_limit",
        "unknown_service",
    }
)


@dataclass
class StoredResult:
    duplicate: bool
    result: dict


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return dict(value or {})


async def execute(
    key: str,
    sub: str,
    action: str,
    fn: Callable[[], Awaitable[dict] | dict],
) -> StoredResult:
    row = db.fetchone("select result from idempotency where key = %s", (key,))
    if row is not None:
        return StoredResult(duplicate=True, result=_as_dict(row["result"]))

    outcome = fn()
    if isinstance(outcome, Awaitable):
        outcome = await outcome
    result = _as_dict(outcome)

    if result.get("error") or result.get("outcome") in _FAIL_OUTCOMES:
        return StoredResult(duplicate=False, result=result)

    if result.get("outcome") == "duplicate":
        return StoredResult(duplicate=True, result=result)

    db.execute(
        "insert into idempotency(key, sub, action, result) values (%s, %s, %s, %s::jsonb)"
        " on conflict (key) do nothing",
        (key, sub, action, json.dumps(result, ensure_ascii=False)),
    )
    return StoredResult(duplicate=False, result=result)
