"""Живая проверка: сбой исполнения (actions остановлен) не даёт повторить операцию мимо PEP.

Шаги (между ними actions останавливают и поднимают снаружи):
  python scripts/probe_actions_down.py card      # сессия + карточка смены тарифа
  python scripts/probe_actions_down.py yes       # «да» при остановленных actions
  python scripts/probe_actions_down.py after     # actions поднят: вопросы с «да» внутри, повторное «да»
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, db  # noqa: E402

STATE = ROOT / ".probe_actions_down.json"


async def turn(c: httpx.AsyncClient, sid: str, message: str) -> list[dict]:
    events: list[dict] = []
    async with c.stream(
        "POST", f"{config.BACKEND_URL}/api/chat", json={"session_id": sid, "message": message}
    ) as resp:
        buf = ""
        async for chunk in resp.aiter_text():
            buf += chunk
            while "\n\n" in buf:
                raw, buf = buf.split("\n\n", 1)
                line = next((l for l in raw.split("\n") if l.startswith("data: ")), None)
                if line:
                    events.append(json.loads(line[6:]))
    return [e for e in events if e.get("type") != "done"]


def text_of(events: list[dict]) -> str:
    return " | ".join(e.get("text", "") for e in events if e.get("type") in ("reply", "error"))


async def tariff(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{config.STAND_URL}/mock/crm/subscriber?sub=u_4f2a")
    return r.json().get("tariff")


def cards_of(sid: str) -> list[dict]:
    return db.fetchall(
        "select pending_id, state from pending_cards where session_id = %s order by created_at",
        (sid,),
    )


def audit_of(sid: str) -> list[str]:
    rows = db.fetchall(
        "select outcome, detail from audit_log where session_id = %s order by id", (sid,)
    )
    return [f"{r['outcome']}:{r['detail'] or ''}" for r in rows]


async def main(step: str) -> int:
    async with httpx.AsyncClient(trust_env=False, timeout=300) as c:
        if step == "card":
            await c.post(f"{config.STAND_URL}/mock/admin/reset")
            sid = (await c.post(f"{config.BACKEND_URL}/api/session", json={"user": "anna"})).json()["session_id"]
            ev = await turn(c, sid, "смени тариф на Семейный")
            got_card = any(e.get("type") == "card" for e in ev)
            STATE.write_text(json.dumps({"sid": sid}), encoding="utf-8")
            print(f"карточка: {got_card}; тариф: {await tariff(c)}; cards={cards_of(sid)}")
            return 0 if got_card else 1

        sid = json.loads(STATE.read_text(encoding="utf-8"))["sid"]

        if step == "yes":
            ev = await turn(c, sid, "да")
            t = await tariff(c)
            print(f"ответ на «да» при падшем actions: {text_of(ev)!r}")
            print(f"тариф: {t}; cards={cards_of(sid)}; audit={audit_of(sid)}")
            ok = t == "Базовый" and any(x["state"] == "failed" for x in cards_of(sid))
            return 0 if ok else 1

        if step == "after":
            fails = 0
            for msg in ("Когда спишут деньги за связь?", "Куда уходят деньги?", "да"):
                ev = await turn(c, sid, msg)
                t = await tariff(c)
                reply = text_of(ev)
                bad = t != "Базовый" or "уже исполнена" in reply or "Готово" in reply
                fails += bad
                print(f"[{'FAIL' if bad else 'ok '}] {msg!r}: тариф={t}; ответ={reply[:90]!r}")
            audit = audit_of(sid)
            executed = [a for a in audit if a.startswith("executed")]
            dup = [a for a in audit if a.startswith("duplicate_suppressed")]
            print(f"cards={cards_of(sid)}")
            print(f"audit={audit}")
            print(f"executed={len(executed)} duplicate_suppressed={len(dup)}")
            return 0 if fails == 0 and not executed and not dup else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "")))
