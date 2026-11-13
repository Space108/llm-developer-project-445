"""N параллельных чат-сессий: живой путь оркестратора под нагрузкой."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, db  # noqa: E402

N = 8


async def one(i: int, c: httpx.AsyncClient) -> dict:
    r = await c.post(f"{config.BACKEND_URL}/api/session", json={"user": "anna"})
    sid = r.json()["session_id"]
    t0 = time.perf_counter()
    events: list[dict] = []
    async with c.stream(
        "POST",
        f"{config.BACKEND_URL}/api/chat",
        json={"session_id": sid, "message": "какой у меня тариф?"},
    ) as resp:
        buf = ""
        async for chunk in resp.aiter_text():
            buf += chunk
            while "\n\n" in buf:
                raw, buf = buf.split("\n\n", 1)
                line = next((l for l in raw.split("\n") if l.startswith("data: ")), None)
                if line:
                    events.append(json.loads(line[6:]))
    text = " ".join(e.get("text", "") for e in events if e.get("type") == "reply")
    return {
        "i": i,
        "ms": int((time.perf_counter() - t0) * 1000),
        "ok": bool(text) and "Базов" in text,
        "text": text[:80],
        "sid": sid,
    }


async def main() -> int:
    async with httpx.AsyncClient(trust_env=False, timeout=180) as c:
        await c.post(f"{config.STAND_URL}/mock/admin/reset")
        marker = time.strftime("%Y-%m-%d %H:%M:%S")
        rows = await asyncio.gather(*[one(i, c) for i in range(N)])
    ok = sum(1 for r in rows if r["ok"])
    for r in rows:
        mark = "ok " if r["ok"] else "FAIL"
        print(f"[{mark}] chat{r['i']} {r['ms']}ms {r['text']!r}")
    print(f"chat parallel: {ok}/{N}")
    providers = db.fetchall(
        "select provider, model, agent, count(*) as n from llm_calls"
        " where ts > now() - interval '3 minutes' group by 1, 2, 3 order by n desc"
    )
    for row in providers:
        print(dict(row))
    del marker
    return 0 if ok == N else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
