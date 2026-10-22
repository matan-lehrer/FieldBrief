"""The LLM gateway — the only path to any LLM (CLAUDE.md "Agent and LLM rules").

Implements design.md "LLM gateway": route task+tier -> model, response cache
for deterministic tasks, budget check, per-provider rate limit, call via
LiteLLM with retries, validate into the caller's Pydantic schema with one
repair retry, settle actual cost, log tokens/$/latency, return a typed object.
"""

import hashlib
import json
import logging
import time
from enum import StrEnum
from typing import TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from fieldbrief.config import settings
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.rate_limit import ProviderRateLimiter

logger = logging.getLogger("fieldbrief.llm.gateway")

T = TypeVar("T", bound=BaseModel)


class Task(StrEnum):
    """What kind of call this is — determines the model tier (CLAUDE.md:
    cheap model for extraction/verification, strong model for synthesis only,
    never in a loop)."""

    EXTRACT = "extract"
    VERIFY = "verify"
    SYNTHESIZE = "synthesize"


TASK_TIER: dict[Task, str] = {
    Task.EXTRACT: "cheap",
    Task.VERIFY: "cheap",
    Task.SYNTHESIZE: "strong",
}


class LLMCallError(Exception):
    """The call failed even after the gateway's retry/repair attempts."""


class LLMGateway:
    def __init__(self, rate_limiter: ProviderRateLimiter | None = None) -> None:
        # Response cache for deterministic tasks (design.md step 2). In-process,
        # per-gateway-instance — a shared cache is scale-path work, not MVP.
        self._cache: dict[str, str] = {}
        self._rate_limiter = rate_limiter or ProviderRateLimiter()

    def model_for(self, task: Task) -> str:
        tier = TASK_TIER[task]
        return settings.llm_model_cheap if tier == "cheap" else settings.llm_model_strong

    async def call(
        self,
        task: Task,
        messages: list[dict[str, str]],
        schema: type[T],
        budget: Budget,
        report_id: str | None = None,
        stage: str | None = None,
        deterministic: bool = False,
    ) -> T:
        model = self.model_for(task)
        provider = litellm.get_llm_provider(model)[1]

        cache_key = None
        if deterministic:
            cache_key = _cache_key(model, messages, schema)
            cached = self._cache.get(cache_key)
            if cached is not None:
                return schema.model_validate_json(cached)

        estimated_cost = _estimate_cost(model, messages)
        budget.reserve(estimated_cost)
        await self._rate_limiter.acquire(provider)

        start = time.monotonic()
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            response_format=schema,
            api_key=settings.llm_api_key,
            num_retries=2,  # covers 429/5xx with LiteLLM's built-in backoff+jitter
        )
        raw = response.choices[0].message.content
        # Settled the instant the real cost is known, regardless of whether
        # the JSON below turns out valid — the call happened and cost money
        # either way; a repair call (if needed) accounts for its own cost.
        cost_usd = litellm.completion_cost(completion_response=response)
        budget.settle(cost_usd - estimated_cost)

        try:
            result = schema.model_validate_json(raw)
        except ValidationError as exc:
            result = await self._repair(model, messages, schema, raw, exc, budget=budget)

        latency_s = time.monotonic() - start

        usage = response.usage
        logger.info(
            "llm_call",
            extra={
                "task": task.value,
                "model": model,
                "provider": provider,
                "report_id": report_id,
                "stage": stage,
                "prompt_tokens": usage.prompt_tokens if usage else None,
                "completion_tokens": usage.completion_tokens if usage else None,
                "cost_usd": cost_usd,
                "latency_s": round(latency_s, 3),
                "budget_remaining_usd": round(budget.remaining_usd(), 4),
            },
        )

        if deterministic and cache_key is not None:
            self._cache[cache_key] = raw

        return result

    async def _repair(
        self,
        model: str,
        messages: list[dict[str, str]],
        schema: type[T],
        bad_output: str,
        error: ValidationError,
        *,
        budget: Budget,
    ) -> T:
        """design.md step 6: "one repair retry." If this also fails, the error
        propagates — the caller (a graph node) treats it as a gap, per ADR 0010.
        A real provider call either way, so it's budget-checked and settled
        just like the original call — it used to be neither, making repairs
        effectively free and uncapped."""
        repair_messages = [
            *messages,
            {"role": "assistant", "content": bad_output},
            {
                "role": "user",
                "content": (
                    f"That response didn't match the required schema: {error}. "
                    "Return only corrected JSON matching the schema, nothing else."
                ),
            },
        ]
        estimated_cost = _estimate_cost(model, repair_messages)
        budget.reserve(estimated_cost)
        response = await litellm.acompletion(
            model=model,
            messages=repair_messages,
            response_format=schema,
            api_key=settings.llm_api_key,
            num_retries=1,
        )
        cost_usd = litellm.completion_cost(completion_response=response)
        budget.settle(cost_usd - estimated_cost)

        raw = response.choices[0].message.content
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            raise LLMCallError(f"repair retry still invalid: {exc}") from exc


def _cache_key(model: str, messages: list[dict[str, str]], schema: type[BaseModel]) -> str:
    payload = json.dumps(
        {"model": model, "messages": messages, "schema": schema.__name__}, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _estimate_cost(model: str, messages: list[dict[str, str]]) -> float:
    """Pre-call estimate for the budget check — approximate on purpose (actual
    cost is settled from real usage after the call). ~4 chars/token is a
    standard rough estimate; good enough for a stop-before-overspend guard."""
    char_count = sum(len(m.get("content", "")) for m in messages)
    estimated_prompt_tokens = max(1, char_count // 4)
    pricing = litellm.model_cost.get(model, {})
    input_cost_per_token = float(pricing.get("input_cost_per_token", 0.0))
    output_cost_per_token = float(pricing.get("output_cost_per_token", 0.0))
    # Assume completion is roughly a third of the prompt size for estimation.
    estimated_completion_tokens = estimated_prompt_tokens // 3
    return (
        estimated_prompt_tokens * input_cost_per_token
        + estimated_completion_tokens * output_cost_per_token
    )
