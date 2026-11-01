"""Клиент guardrail-сервиса: verdikt вероятностный, решения — за кодом.

Verdict(safe, category, available) и check(text, mode):
  POST {GUARDRAIL_URL}/check {"text", "mode": "input"|"output"}.
  Недоступность сервиса — НЕ исключение: Verdict(safe=True,
  available=False) — чтение продолжается, действия блокируются флагом
  guard_ok в политике (fail-closed).
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from core import config


@dataclass
class Verdict:
    safe: bool
    category: str = ""
    available: bool = True


async def check(text: str, mode: str = "input") -> Verdict:
    """Проверка текста. Сбой / 5xx → available=False, safe=True (деградация)."""
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
            r = await client.post(
                f"{config.GUARDRAIL_URL}/check",
                json={"text": text, "mode": mode},
            )
            if r.status_code != 200:
                return Verdict(safe=True, available=False)
            data = r.json()
            return Verdict(
                safe=str(data.get("verdict") or "") == "safe",
                category=str(data.get("category") or ""),
                available=True,
            )
    except Exception:  # noqa: BLE001 — недоступность не роняет ход
        return Verdict(safe=True, available=False)
