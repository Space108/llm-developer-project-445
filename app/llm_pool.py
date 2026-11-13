"""Пул из двух OpenAI-совместимых провайдеров с ручной политикой failover.

Включается, когда в .env задан LLM_SECONDARY_BASE_URL. Агенты не
пересобираются: клиент и модель провайдера передаются в RunConfig
конкретного Runner.run, глобально ничего не переключается.

Политика (один обход пула):
  - повторять: 429, 5xx, timeout, сетевая ошибка;
  - не повторять: 400, 401, 403 (ошибка вызова);
  - провайдер с 429/5xx уходит в cooldown на LLM_FAILOVER_COOLDOWN_SEC.
"""
from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from core import config

T = TypeVar("T")

_POOL: LlmPool | None = None


@dataclass
class Provider:
    name: str
    model: str
    client: Any
    price_rub_per_1m: float
    cooldown_until: float = 0.0

    @property
    def available(self) -> bool:
        return time.monotonic() >= self.cooldown_until

    def cool_down(self, seconds: float) -> None:
        self.cooldown_until = time.monotonic() + max(0.0, seconds)


@dataclass
class CallMeta:
    provider: str
    model: str
    price_rub_per_1m: float


@dataclass
class LlmPool:
    providers: list[Provider] = field(default_factory=list)
    cooldown_sec: float = 60.0
    last_meta: CallMeta | None = None

    def activate(self, provider: Provider) -> None:
        from agents import set_default_openai_client

        set_default_openai_client(provider.client, use_for_tracing=False)
        self.last_meta = CallMeta(
            provider=provider.name,
            model=provider.model,
            price_rub_per_1m=provider.price_rub_per_1m,
        )


def enabled() -> bool:
    return bool(config.LLM_SECONDARY_BASE_URL and config.LLM_SECONDARY_MODEL)


def get_pool() -> LlmPool | None:
    return _POOL


def setup_pool() -> LlmPool | None:
    """Собрать primary+secondary и выставить primary как default-клиент."""
    global _POOL
    if not enabled():
        _POOL = None
        return None

    try:
        import httpx2 as httpx
    except ImportError:  # pragma: no cover
        import httpx
    from agents import set_default_openai_api, set_tracing_disabled
    from openai import AsyncOpenAI

    primary_base = (os.environ.get("LLM_BASE_URL") or "").strip()
    primary_model = (os.environ.get("LLM_MODEL") or "").strip()
    if not primary_base or not primary_model:
        raise SystemExit("LLM_BASE_URL/LLM_MODEL нужны и при включённом пуле")

    timeout = httpx.Timeout(180.0, connect=30.0)
    primary = Provider(
        name="primary",
        model=primary_model,
        client=AsyncOpenAI(
            base_url=primary_base,
            api_key=os.environ.get("LLM_API_KEY", "none"),
            http_client=httpx.AsyncClient(trust_env=False, timeout=timeout),
        ),
        price_rub_per_1m=config.LLM_PRICE_RUB_PER_1M,
    )
    secondary = Provider(
        name="secondary",
        model=config.LLM_SECONDARY_MODEL,
        client=AsyncOpenAI(
            base_url=config.LLM_SECONDARY_BASE_URL,
            api_key=config.LLM_SECONDARY_API_KEY or "none",
            http_client=httpx.AsyncClient(trust_env=False, timeout=timeout),
        ),
        price_rub_per_1m=config.LLM_SECONDARY_PRICE_RUB_PER_1M,
    )
    pool = LlmPool(
        providers=[primary, secondary],
        cooldown_sec=float(config.LLM_FAILOVER_COOLDOWN_SEC),
    )
    set_default_openai_api("chat_completions")
    set_tracing_disabled(True)
    pool.activate(primary)
    config.MODEL_NAME = primary.model
    _POOL = pool
    return pool


def classify_error(exc: BaseException) -> str:
    """'retry_cooldown' | 'retry' | 'fatal' — для политики и смоков."""
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)

    name = type(exc).__name__
    module = type(exc).__module__ or ""

    if status in {400, 401, 403}:
        return "fatal"
    if status == 429 or (isinstance(status, int) and status >= 500):
        return "retry_cooldown"
    if name in {
        "APITimeoutError",
        "APIConnectionError",
        "TimeoutError",
        "ConnectError",
        "ReadTimeout",
        "ConnectTimeout",
        "NetworkError",
    }:
        return "retry"
    if "timeout" in name.lower() or "connection" in name.lower():
        return "retry"
    if status is None and ("openai" in module or "httpx" in module):
        return "retry"
    if status is None:
        return "retry"
    return "fatal"


async def _over_pool(pool: LlmPool, fn: Callable[[Provider], Awaitable[T]]) -> tuple[T, Provider]:
    """Один обход пула: fn получает провайдера попытки; глобального состояния нет."""
    last_exc: BaseException | None = None
    ordered = [p for p in pool.providers if p.available] or list(pool.providers)

    for provider in ordered:
        try:
            return await fn(provider), provider
        except BaseException as exc:  # noqa: BLE001 — классифицируем и решаем
            kind = classify_error(exc)
            last_exc = exc
            if kind == "fatal":
                raise
            if kind == "retry_cooldown":
                provider.cool_down(pool.cooldown_sec)
            continue

    assert last_exc is not None
    raise last_exc


async def call(fn: Callable[[], Awaitable[T]]) -> T:
    """Выполнить fn с политикой переключения через default-клиент SDK. Один обход пула."""
    pool = _POOL
    if pool is None or not pool.providers:
        return await fn()

    async def _attempt(provider: Provider) -> T:
        pool.activate(provider)
        return await fn()

    result, _provider = await _over_pool(pool, _attempt)
    return result


async def run_with_failover(agent: Any, task: Any) -> tuple[Any, CallMeta]:
    """Runner.run под пулом: клиент и модель попытки — в RunConfig этого запуска.

    Default-клиент и agent.model не трогаем: параллельные сессии не видят
    чужое переключение, provider в llm_calls — тот, кто реально ответил.
    """
    from agents import OpenAIChatCompletionsModel, RunConfig, Runner

    pool = _POOL
    if pool is None:
        result = await Runner.run(agent, task)
        model = getattr(agent, "model", None) or config.MODEL_NAME or ""
        meta = CallMeta(
            provider="primary",
            model=model,
            price_rub_per_1m=config.price_rub_per_1m(model),
        )
        return result, meta

    async def _attempt(provider: Provider) -> Any:
        run_config = RunConfig(
            model=OpenAIChatCompletionsModel(model=provider.model, openai_client=provider.client),
            tracing_disabled=True,
        )
        return await Runner.run(agent, task, run_config=run_config)

    result, provider = await _over_pool(pool, _attempt)
    meta = CallMeta(
        provider=provider.name,
        model=provider.model,
        price_rub_per_1m=provider.price_rub_per_1m,
    )
    return result, meta
