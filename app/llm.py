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

from agents import Agent, AgentOutputSchema, Runner
from agents.items import ToolCallItem, ToolCallOutputItem

from core import config


def build_agent(
    name: str,
    instructions: str,
    tools: list | None = None,
    output_type: type | None = None,
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
    return Agent(**kwargs)


async def run_agent(agent: Agent, task: str, *, trace_id: str = "") -> dict:
    del trace_id  # учёт в llm_calls — шаг 3
    started = time.perf_counter()
    result = await Runner.run(agent, task)
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
    return {
        "report": report,
        "tools": tools,
        "seconds": time.perf_counter() - started,
    }
