"""Права сессии: allowlist из claims, два барьера оркестратора.

Константы операций и ключей идентичности; allowlist_from_claims;
visible_tools (барьер 1); admit (барьер 2). Лимиты и подтверждения —
на шаге политики (PDP).
"""
from __future__ import annotations

from dataclasses import dataclass, field

READ_OPS = (
    "get_my_profile",
    "get_my_tariff",
    "list_tariffs",
    "list_my_charges",
    "search_kb",
)
WRITE_OPS = (
    "prepare_tariff_change",
    "prepare_toggle_service",
    "prepare_contacts_update",
)
OPERATOR_OPS = ("get_subscriber_overview",)
ESCALATION_OPS = ("create_ticket",)
# phone не ключ: новое значение контакта в prepare_contacts_update
IDENTITY_ARGS = frozenset({"user_id", "account", "subscriber", "msisdn", "sub"})


@dataclass
class Allowlist:
    sub: str
    role: str
    scopes: list[str] = field(default_factory=list)
    read_ops: list[str] = field(default_factory=list)
    write_ops: list[str] = field(default_factory=list)


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


def _scopes_from_claims(claims: dict) -> list[str]:
    raw = claims.get("scope", claims.get("scopes", ""))
    if isinstance(raw, list):
        return [str(s) for s in raw]
    return str(raw or "").split()


def allowlist_from_claims(claims: dict) -> Allowlist:
    """Роль даёт базовый набор, scope сужает write; оператору write нет."""
    sub = str(claims.get("sub") or "")
    role = str(claims.get("role") or "")
    scopes = _scopes_from_claims(claims)

    read_ops = list(READ_OPS) + list(ESCALATION_OPS)
    write_ops: list[str] = []

    if role == "operator":
        read_ops = list(READ_OPS) + list(OPERATOR_OPS) + list(ESCALATION_OPS)
    elif role == "client" and "tariffs:write" in scopes:
        write_ops = list(WRITE_OPS)

    return Allowlist(
        sub=sub,
        role=role,
        scopes=scopes,
        read_ops=read_ops,
        write_ops=write_ops,
    )


def visible_tools(allowlist: Allowlist, names: list[str]) -> list[str]:
    """Барьер 1: модель видит только разрешённое; execute_action — никому."""
    allowed = set(allowlist.read_ops) | set(allowlist.write_ops)
    allowed.discard("execute_action")
    return [n for n in names if n in allowed]


def admit(allowlist: Allowlist, operation: str, args: dict | None = None) -> Decision:
    """Барьер 2: тройка субъект/операция/объект в момент вызова."""
    args = args or {}
    for key in args:
        if key in IDENTITY_ARGS:
            return Decision(False, f"object_from_arguments:{key}")

    allowed_ops = set(allowlist.read_ops) | set(allowlist.write_ops)
    if operation not in allowed_ops:
        return Decision(False, f"operation_not_allowed:{operation}")
    return Decision(True, "")
