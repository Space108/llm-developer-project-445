"""PEP: оркестратор спрашивает PDP — сам решений о write-пути не принимает."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from core import config


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


@dataclass
class Pep:
    session_id: str
    claims: dict
    guard_state: dict = field(default_factory=lambda: {"ok": True})
    _opened: bool = False

    def _scopes(self) -> list[str]:
        raw = self.claims.get("scope", self.claims.get("scopes", ""))
        if isinstance(raw, list):
            return [str(s) for s in raw]
        return str(raw or "").split()

    def _subject(self) -> dict:
        return {
            "sub": str(self.claims.get("sub") or ""),
            "role": str(self.claims.get("role") or ""),
            "scopes": self._scopes(),
        }

    def _guard_ok(self) -> bool:
        return bool((self.guard_state or {}).get("ok", True))

    def _input(self, card) -> dict:
        subject = self._subject()
        return {
            "action": card.action,
            "pending_id": card.pending_id,
            "details": json.dumps(card.details or {}, ensure_ascii=False),
            "guard_ok": self._guard_ok(),
            **subject,
        }

    async def _open(self) -> None:
        if self._opened:
            return
        async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
            r = await client.post(
                f"{config.PDP_URL}/session",
                json={"session_id": self.session_id, "subject": self._subject()},
            )
            r.raise_for_status()
        self._opened = True

    async def _post(self, path: str, body: dict) -> Decision:
        try:
            await self._open()
            async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
                r = await client.post(f"{config.PDP_URL}{path}", json=body)
                r.raise_for_status()
                data = r.json()
        except Exception:  # noqa: BLE001 — fail-closed
            return Decision(False, "pdp_unavailable")
        return Decision(data.get("decision") == "Allow", str(data.get("reason") or ""))

    async def prepare(self, card) -> Decision:
        return await self._post(
            "/authorize",
            {"session_id": self.session_id, "action": "Prepare", "input": self._input(card)},
        )

    async def confirm(self, card) -> Decision:
        return await self._post(
            "/confirm",
            {"session_id": self.session_id, "input": self._input(card)},
        )

    async def execute(self, card) -> Decision:
        return await self._post(
            "/authorize",
            {"session_id": self.session_id, "action": "Execute", "input": self._input(card)},
        )
