"""Сборка агентов на модели из .env + учёт LLM-вызовов.

build_agent берёт модель из config; пул failover встраивается на уровне
клиента (app.llm_pool), агентов не пересобирает. run_agent пишет в
llm_calls фактические provider/model и cost_rub по цене этой модели.
"""
from __future__ import annotations

import time
from typing import Any

from agents import Agent, AgentOutputSchema, ModelSettings
from agents.items import ToolCallItem, ToolCallOutputItem

from app import llm_pool
from core import config, db


def build_agent(
    name: str,
    instructions: str,
    tools: list | None = None,
    output_type: type | None = None,
    model_settings: ModelSettings | None = None,
) -> Agent:
    model = config.MODEL_NAME or config.setup_model()
    kwargs: dict[str, Any] = {
        "name": name,
        "instructions": instructions,
        "model": model,
        "tools": tools or [],
    }
    if output_type is not None:
        kwargs["output_type"] = AgentOutputSchema(output_type, strict_json_schema=False)
    if model_settings is not None:
        kwargs["model_settings"] = model_settings
    return Agent(**kwargs)


def _usage_tokens(result) -> tuple[int | None, int | None]:
    wrapper = getattr(result, "context_wrapper", None)
    usage = getattr(wrapper, "usage", None) if wrapper is not None else None
    if usage is None:
        return None, None
    inp = getattr(usage, "input_tokens", None)
    if inp is None:
        inp = getattr(usage, "prompt_tokens", None)
    out = getattr(usage, "output_tokens", None)
    if out is None:
        out = getattr(usage, "completion_tokens", None)
    return inp, out


def _cost_rub(inp: int | None, out: int | None, price_per_1m: float) -> float | None:
    if inp is None and out is None:
        return None
    tokens = (inp or 0) + (out or 0)
    return round(tokens / 1_000_000.0 * price_per_1m, 6)


async def run_agent(agent: Agent, task: str, *, trace_id: str = "") -> dict:
    started = time.perf_counter()
    result, meta = await llm_pool.run_with_failover(agent, task)
    latency_ms = int((time.perf_counter() - started) * 1000)
    tools: dict[str, dict] = {}
    pending: dict[str, dict] = {}
    for item in result.new_items:
        if isinstance(item, ToolCallItem):
            raw = item.raw_item
            args = getattr(raw, "arguments", None)
            if args is None and isinstance(raw, dict):
                args = raw.get("arguments")
            cid = item.call_id or f"call_{len(pending)}"
            pending[cid] = {"name": item.tool_name, "arguments": args or ""}
        elif isinstance(item, ToolCallOutputItem):
            cid = item.call_id or f"out_{len(tools)}"
            meta_tool = pending.get(cid, {})
            tools[cid] = {
                "name": meta_tool.get("name"),
                "arguments": meta_tool.get("arguments"),
                "output": item.output,
            }
    report = result.final_output
    if report is None:
        report = ""
    elif not isinstance(report, str):
        report = str(report)

    inp, out = _usage_tokens(result)
    cost = _cost_rub(inp, out, meta.price_rub_per_1m)
    db.execute(
        "insert into llm_calls(trace_id, agent, model, input_tokens, output_tokens, latency_ms, provider, cost_rub)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            trace_id or None,
            getattr(agent, "name", "agent"),
            meta.model or config.MODEL_NAME or "",
            inp,
            out,
            latency_ms,
            meta.provider,
            cost,
        ),
    )
    return {
        "report": report,
        "tools": tools,
        "seconds": latency_ms / 1000.0,
        "provider": meta.provider,
        "model": meta.model,
    }
