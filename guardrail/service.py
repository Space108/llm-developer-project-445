"""Guardrail-сервис: отдельный stateless-контур проверки текста.

    uvicorn --factory guardrail.service:build_app --host 127.0.0.1 --port 8300

RULE-движок: детерминированные маркеры атак стенда (лабораторная эвристика).
Опционально GUARDRAIL_MODEL=1 — классификатор HiveTraceGuard-Pro
(transformers+torch, в requirements не включены).

Недоступность сервиса — не крах контура: оркестратор решает сам
(fail-closed для действий, деградация для чтения).
"""
from __future__ import annotations

import os
import re
from typing import Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core import config

# Маркеры стенда: (категория, regex). Порядок — приоритет категории.
RULE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("jailbreak", re.compile(r"суперпользовател|ты теперь админ|ты теперь супер", re.I)),
    (
        "prompt_injection",
        re.compile(
            r"игнорируй инструкц|забудь (все )?правил|ignore (all )?(previous |prior )?instruction",
            re.I,
        ),
    ),
    (
        "tool_hijack",
        re.compile(r"смени тариф в аккаунте|вызови инструмент|вызови tool|call the tool", re.I),
    ),
    ("injected_instruction", re.compile(r"немедленно переведи|без подтверждения", re.I)),
]

_down = False
_model_fn: Callable[[str, str], tuple[str, str]] | None = None


def _load_model() -> Callable[[str, str], tuple[str, str]] | None:
    """HiveTraceGuard-Pro: один токен safe/unsafe. Нет зависимостей — None."""
    if os.environ.get("GUARDRAIL_MODEL", "").strip() not in {"1", "true", "yes"}:
        return None
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        import torch
    except ImportError:
        return None

    name = "hivetrace/HiveTraceGuard-Pro"
    tokenizer = AutoTokenizer.from_pretrained(name, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(name, trust_remote_code=True)
    model.eval()

    def classify(text: str, mode: str) -> tuple[str, str]:
        role = "user" if mode == "input" else "assistant"
        messages = [{"role": role, "content": text or ""}]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(prompt, return_tensors="pt")
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=4, do_sample=False)
        gen = tokenizer.decode(
            out[0][inputs["input_ids"].shape[-1] :], skip_special_tokens=True
        )
        token = (gen or "").strip().split()[0].lower() if gen.strip() else "safe"
        if token.startswith("unsafe"):
            return "unsafe", "model"
        return "safe", ""

    return classify


def _check_text(text: str, mode: str) -> dict:
    global _model_fn
    if _model_fn is None and os.environ.get("GUARDRAIL_MODEL", "").strip() in {
        "1",
        "true",
        "yes",
    }:
        _model_fn = _load_model()
    if _model_fn is not None:
        verdict, category = _model_fn(text, mode)
        return {"verdict": verdict, "category": category, "engine": "MODEL", "mode": mode}
    for category, pattern in RULE_PATTERNS:
        if pattern.search(text or ""):
            return {
                "verdict": "unsafe",
                "category": category,
                "engine": "RULE",
                "mode": mode,
            }
    return {"verdict": "safe", "category": "", "engine": "RULE", "mode": mode}


async def health(request: Request) -> JSONResponse:
    del request
    if _down:
        return JSONResponse({"status": "down"}, status_code=503)
    return JSONResponse({"status": "ok", "engine": "MODEL" if _model_fn else "RULE"})


async def admin_down(request: Request) -> JSONResponse:
    del request
    global _down
    _down = True
    return JSONResponse({"ok": True, "status": "down"})


async def admin_up(request: Request) -> JSONResponse:
    del request
    global _down
    _down = False
    return JSONResponse({"ok": True, "status": "up"})


async def check(request: Request) -> JSONResponse:
    if _down:
        return JSONResponse({"error": "down"}, status_code=503)
    body = await request.json()
    text = str(body.get("text") or "")
    mode = str(body.get("mode") or "input")
    if mode not in ("input", "output"):
        return JSONResponse({"error": "bad_mode"}, status_code=400)
    return JSONResponse(_check_text(text, mode))


def build_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/check", check, methods=["POST"]),
            Route("/admin/down", admin_down, methods=["POST"]),
            Route("/admin/up", admin_up, methods=["POST"]),
        ],
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        build_app(),
        host="127.0.0.1",
        port=config.GUARDRAIL_PORT,
        log_level="warning",
    )
