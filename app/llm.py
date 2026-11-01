"""Сборка агентов на модели из .env + учёт LLM-вызовов.

TODO(шаг 1):
  1. build_agent(name, instructions, tools=None, output_type=None):
     Agent из agents SDK; output_type — нестрогая схема
     (AgentOutputSchema(output_type, strict_json_schema=False)):
     строгий JSON-режим слишком требователен к малым моделям;
     модель — config.MODEL_NAME или config.setup_model().
  2. run_agent(agent, task, *, trace_id="") → {"report", "tools", "seconds"}:
     отдельный Runner.run; из result.new_items собрать tool-вызовы
     (call_id → arguments/output); report — result.final_output строкой.

TODO(шаг 3): в run_agent — INSERT в llm_calls: trace_id, имя агента,
  модель, токены (result.context_wrapper.usage), латентность.

TODO(шаг 8*): если llm_pool.enabled() — запуск через
  llm_pool.run_with_failover, в llm_calls — фактический провайдер.
"""
from __future__ import annotations

import time
from typing import Any

from agents import Agent, AgentOutputSchema, ModelSettings, Runner
from agents.items import ToolCallItem, ToolCallOutputItem

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


async def run_agent(agent: Agent, task: str, *, trace_id: str = "") -> dict:
    started = time.perf_counter()
    result = await Runner.run(agent, task)
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
            meta = pending.get(cid, {})
            tools[cid] = {
                "name": meta.get("name"),
                "arguments": meta.get("arguments"),
                "output": item.output,
            }
    report = result.final_output
    if report is None:
        report = ""
    elif not isinstance(report, str):
        report = str(report)

    inp, out = _usage_tokens(result)
    db.execute(
        "insert into llm_calls(trace_id, agent, model, input_tokens, output_tokens, latency_ms)"
        " values (%s, %s, %s, %s, %s, %s)",
        (
            trace_id or None,
            getattr(agent, "name", "agent"),
            config.MODEL_NAME or "",
            inp,
            out,
            latency_ms,
        ),
    )
    return {
        "report": report,
        "tools": tools,
        "seconds": latency_ms / 1000.0,
    }
