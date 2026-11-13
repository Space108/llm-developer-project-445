"""Параллельная нагрузка на llm_pool: совпадает ли meta.provider с фактическим клиентом.

Два прогона:
  1) фейковые провайдеры — гонка на переключении (без сети);
  2) живой пул из .env — N одновременных Runner.run.

Запуск: python scripts/probe_pool_parallel.py
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import llm_pool  # noqa: E402
from core import config, db  # noqa: E402


N_FAKE = 40
N_LIVE = 12
CONCURRENCY = 8


async def probe_fake() -> tuple[int, int]:
    """Фейки с разной задержкой: meta обязана совпасть с тем, кто реально ответил."""
    hits: list[str] = []
    lock = asyncio.Lock()

    class FakeClient:
        def __init__(self, name: str, delay: float):
            self.name = name
            self.delay = delay

    primary = llm_pool.Provider("primary", "m-primary", FakeClient("primary", 0.05), 1.0)
    secondary = llm_pool.Provider("secondary", "m-secondary", FakeClient("secondary", 0.02), 2.0)
    pool = llm_pool.LlmPool(providers=[primary, secondary], cooldown_sec=0.0)
    llm_pool._POOL = pool

    real_runner_mod = None

    async def fake_run(agent, task, run_config=None):
        # кто в RunConfig — тот и «ответил» (клиент лежит в _client модели SDK)
        model_obj = getattr(run_config, "model", None)
        client = getattr(model_obj, "_client", None) or getattr(model_obj, "openai_client", None)
        name = getattr(client, "name", "?")
        delay = getattr(client, "delay", 0.01)
        # середина волны: primary уходит в cooldown — часть запросов уже в secondary
        if name == "primary" and len(hits) == N_FAKE // 4:
            primary.cool_down(30.0)
        await asyncio.sleep(delay)
        async with lock:
            hits.append(name)
        return SimpleNamespace(
            final_output=name,
            context_wrapper=None,
            new_items=[],
            to_input_list=lambda: [],
        )

    # подмена Runner внутри run_with_failover
    import agents

    original = agents.Runner.run
    agents.Runner.run = fake_run
    try:
        agent = SimpleNamespace(name="probe", model="ignored", tools=[])
        tasks = [llm_pool.run_with_failover(agent, f"t{i}") for i in range(N_FAKE)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        agents.Runner.run = original
        llm_pool._POOL = None

    ok = 0
    bad = 0
    for r in results:
        if isinstance(r, Exception):
            bad += 1
            print(f"  fake FAIL: {r!r}")
            continue
        result, meta = r
        claimed = meta.provider
        actual = result.final_output
        if claimed == actual:
            ok += 1
        else:
            bad += 1
            print(f"  mismatch: meta={claimed} actual={actual}")
    print(f"fake: {ok}/{N_FAKE} совпали, hits={len(hits)}")
    return ok, bad


async def one_live(i: int, sem: asyncio.Semaphore) -> dict:
    from agents import Agent

    async with sem:
        agent = Agent(
            name=f"probe_{i}",
            instructions="Ответь одним словом: ok",
            model=config.MODEL_NAME,
        )
        started = time.perf_counter()
        trace_id = f"tr_probe_{uuid.uuid4().hex[:10]}"
        try:
            result, meta = await llm_pool.run_with_failover(agent, "Скажи ok")
            from app import llm

            llm.log_call(
                agent, result, meta, int((time.perf_counter() - started) * 1000), trace_id
            )
            text = str(result.final_output or "")[:40]
            return {
                "ok": True,
                "provider": meta.provider,
                "model": meta.model,
                "trace_id": trace_id,
                "text": text,
                "ms": int((time.perf_counter() - started) * 1000),
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": repr(e)[:160], "trace_id": trace_id}


async def probe_live() -> list[dict]:
    pool = llm_pool.setup_pool()
    if pool is None:
        print("live: пул выключен (нет LLM_SECONDARY_*) — пропуск")
        return []
    print(
        f"live: primary={pool.providers[0].model} secondary={pool.providers[1].model}, "
        f"N={N_LIVE} concurrency={CONCURRENCY}"
    )
    sem = asyncio.Semaphore(CONCURRENCY)
    rows = await asyncio.gather(*[one_live(i, sem) for i in range(N_LIVE)])
    by_provider: dict[str, int] = {}
    ok = sum(1 for r in rows if r.get("ok"))
    for r in rows:
        if r.get("ok"):
            by_provider[r["provider"]] = by_provider.get(r["provider"], 0) + 1
            print(f"  {r['provider']:9} {r['model']:28} {r['ms']:5}ms  {r['text']!r}")
        else:
            print(f"  FAIL {r.get('error')}")
    # сверка: строка в llm_calls совпадает с тем, что вернул meta
    mismatch = 0
    for r in rows:
        if not r.get("ok"):
            continue
        row = db.fetchone(
            "select provider, model from llm_calls where trace_id = %s order by id desc limit 1",
            (r["trace_id"],),
        )
        if row is None or row["provider"] != r["provider"] or row["model"] != r["model"]:
            mismatch += 1
            print(f"  journal mismatch {r['trace_id']}: {row} vs {r['provider']}/{r['model']}")
    print(f"live: ok={ok}/{N_LIVE} providers={by_provider} journal_mismatch={mismatch}")
    return rows


async def main() -> int:
    print("── фейковая гонка ──")
    fake_ok, fake_bad = await probe_fake()
    print("── живой пул ──")
    live = await probe_live()
    live_fail = sum(1 for r in live if not r.get("ok"))
    if fake_bad or live_fail:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
