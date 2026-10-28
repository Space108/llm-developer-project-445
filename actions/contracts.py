"""Контракты действий: схемы, нормализация, таксономия ошибок.

TODO(шаг 2):
  1. ActionError(code, message, **hints) — отказ действия:
     to_tool_result() → {"error": code, "detail": message, **hints}.
  2. BUSINESS_RULE_CODES = same_tariff, tariff_not_available,
     service_already_on/off, same_contacts — повторять бессмысленно.
  3. Нормализация «как услышали» → точное значение:
     normalize_tariff(raw, catalog) — unknown_tariff с подсказкой
     available=[...]; normalize_service(raw, catalog) — unknown_service;
     normalize_phone — 11 цифр → bad_phone с примером; normalize_email.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

BUSINESS_RULE_CODES = frozenset(
    {
        "same_tariff",
        "tariff_not_available",
        "service_already_on",
        "service_already_off",
        "same_contacts",
        "nothing_to_update",
    }
)


class ActionError(Exception):
    def __init__(self, code: str, message: str = "", **hints) -> None:
        self.code = code
        self.message = message or code
        self.hints = hints
        super().__init__(self.message)

    def to_tool_result(self) -> dict:
        return {"error": self.code, "detail": self.message, **self.hints}


def normalize_tariff(raw: str, catalog: list[dict]) -> str:
    """«семейный» / «Семейный» → точное имя из каталога."""
    available = [t["name"] for t in catalog if t.get("available", True)]
    all_names = [t["name"] for t in catalog]
    needle = (raw or "").strip().strip("«»\"'").lower()
    for name in all_names:
        if name.lower() == needle:
            row = next(t for t in catalog if t["name"] == name)
            if not row.get("available", True):
                raise ActionError(
                    "tariff_not_available",
                    f"тариф «{name}» недоступен для подключения",
                    tariff=name,
                    available=available,
                )
            return name
    raise ActionError(
        "unknown_tariff",
        f"тариф «{raw}» не найден",
        available=available,
    )


def normalize_service(raw: str, catalog: list[str]) -> str:
    needle = (raw or "").strip().strip("«»\"'").lower()
    for name in catalog:
        if name.lower() == needle:
            return name
    raise ActionError(
        "unknown_service",
        f"услуга «{raw}» не найдена",
        available=list(catalog),
    )


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in "78":
        return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    if len(digits) == 10:
        return f"+7 {digits[0:3]} {digits[3:6]}-{digits[6:8]}-{digits[8:10]}"
    raise ActionError(
        "bad_phone",
        "телефон должен содержать 11 цифр",
        example="+7 900 111-22-33",
    )


def normalize_email(raw: str) -> str:
    value = (raw or "").strip()
    if "@" not in value or "." not in value.split("@")[-1]:
        raise ActionError("bad_email", "нужен адрес вида name@example.ru", example="anna@example.ru")
    return value.lower()


class ActionCard(BaseModel):
    action: str
    details: dict
    view: dict
