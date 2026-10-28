"""Логика действий поверх REST-моков: предусловия, карточки, исполнение.

TODO(шаг 2): prepare_* ничего не исполняют — проверяют предусловия и
  собирают view для карточки («с какого на какой, что изменится»):

  prepare_tariff_change(sub, tariff_raw):
    текущий тариф → GET /mock/crm/subscriber?sub=...; каталог →
    GET /mock/crm/tariffs; normalize_tariff; available? same_tariff?
    → {"action": "tariff_change", "details": {"tariff": ...},
       "view": {...}}.
  prepare_toggle_service(sub, service, enabled): unknown_service /
    service_already_on / service_already_off.
  prepare_contacts_update(sub, phone, email): нормализация,
    nothing_to_update / same_contacts.

  execute_action(sub, action, details_json, idempotency_key) —
  ИСПОЛНЕНИЕ; вызывает только код оркестратора после «да»:
    tariff_change → POST /mock/billing/tariff-change (идемпотентность и
      дневной лимит держит сам биллинг: executed/duplicate/daily_limit);
    service_toggle → POST /mock/crm/toggle-service;
    contacts_update → POST /mock/crm/contacts.
  create_ticket(sub, subject, note) → POST /mock/tickets/create.

Все запросы — через httpx с trust_env=False. Субъект sub приходит
аргументом на шагах 2–3 (осознанная дыра): шаг прав её закроет.
"""
from __future__ import annotations

import json

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from actions.contracts import (
    ActionError,
    normalize_email,
    normalize_phone,
    normalize_service,
    normalize_tariff,
)
from core import config

SERVICE_CATALOG = ["Музыка", "Роуминг", "Антивирус"]

_CLIENT = httpx.Client(trust_env=False, timeout=30)


def _get(path: str, **params) -> dict:
    r = _CLIENT.get(f"{config.STAND_URL}{path}", params=params)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict) -> dict:
    r = _CLIENT.post(f"{config.STAND_URL}{path}", json=body)
    r.raise_for_status()
    return r.json()


def prepare_tariff_change(sub: str, tariff_raw: str) -> dict:
    profile = _get("/mock/crm/subscriber", sub=sub)
    catalog = _get("/mock/crm/tariffs").get("tariffs", [])
    target = normalize_tariff(tariff_raw, catalog)
    current = profile.get("tariff")
    if current == target:
        raise ActionError("same_tariff", f"уже на тарифе «{current}»", current=current)

    cur_row = next((t for t in catalog if t["name"] == current), None)
    new_row = next(t for t in catalog if t["name"] == target)
    cur_price = int(cur_row["price_rub"]) if cur_row else 0
    new_price = int(new_row["price_rub"])
    return {
        "ok": True,
        "action": "tariff_change",
        "details": {"tariff": target},
        "view": {
            "что": "СМЕНА ТАРИФА",
            "с": current,
            "на": target,
            "было": f"{cur_price} ₽/мес",
            "станет": f"{new_price} ₽/мес",
        },
    }


def prepare_toggle_service(sub: str, service: str, enabled: bool) -> dict:
    name = normalize_service(service, SERVICE_CATALOG)
    profile = _get("/mock/crm/subscriber", sub=sub)
    services = {s["service"]: s for s in profile.get("services", [])}
    row = services.get(name)
    if row is None:
        # услуга из каталога, но нет строки у абонента — считаем выключенной
        currently = False
    else:
        currently = bool(row.get("enabled"))
    if enabled and currently:
        raise ActionError("service_already_on", f"услуга «{name}» уже включена", service=name)
    if not enabled and not currently:
        raise ActionError("service_already_off", f"услуга «{name}» уже выключена", service=name)
    return {
        "ok": True,
        "action": "service_toggle",
        "details": {"service": name, "enabled": bool(enabled)},
        "view": {
            "что": "услуга",
            "услуга": name,
            "действие": "включить" if enabled else "выключить",
        },
    }


def prepare_contacts_update(sub: str, phone: str | None = None, email: str | None = None) -> dict:
    if not phone and not email:
        raise ActionError("nothing_to_update", "нечего менять: укажите телефон и/или почту")
    profile = _get("/mock/crm/subscriber", sub=sub)
    details: dict = {}
    view: dict = {"что": "контакты"}
    if phone:
        details["phone"] = normalize_phone(phone)
        view["телефон"] = details["phone"]
    if email:
        details["email"] = normalize_email(email)
        view["почта"] = details["email"]
    same = True
    if "phone" in details and details["phone"] != profile.get("phone"):
        same = False
    if "email" in details and details["email"] != profile.get("email"):
        same = False
    if same:
        raise ActionError("same_contacts", "контакты уже такие", **details)
    return {"ok": True, "action": "contacts_update", "details": details, "view": view}


def execute_action(
    sub: str,
    action: str,
    details_json: str,
    idempotency_key: str,
    pending_id: str = "",
) -> dict:
    del pending_id  # ключ идемпотентности задаёт оркестратор; pending_id — для трассировки
    try:
        details = json.loads(details_json) if isinstance(details_json, str) else dict(details_json)
    except (TypeError, ValueError) as e:
        raise ActionError("bad_details", "details_json — не JSON") from e

    if action == "tariff_change":
        return _post(
            "/mock/billing/tariff-change",
            {
                "sub": sub,
                "tariff": details.get("tariff"),
                "idempotency_key": idempotency_key,
            },
        )
    if action == "service_toggle":
        return _post(
            "/mock/crm/toggle-service",
            {
                "sub": sub,
                "service": details.get("service"),
                "enabled": bool(details.get("enabled")),
            },
        )
    if action == "contacts_update":
        body = {"sub": sub}
        if "phone" in details:
            body["phone"] = details["phone"]
        if "email" in details:
            body["email"] = details["email"]
        return _post("/mock/crm/contacts", body)
    raise ActionError("unknown_action", f"неизвестное действие: {action}")


def create_ticket(sub: str, subject: str, note: str = "") -> dict:
    return _post(
        "/mock/tickets/create",
        {"sub": sub, "subject": subject or "обращение", "note": note or ""},
    )
