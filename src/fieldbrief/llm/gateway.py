import hashlib
import json
import logging
import time
from enum import StrEnum
from typing import TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from fieldbrief.config import settings
from fieldbrief.consts import EXTRACT_NUM_RETRIES, REPAIR_NUM_RETRIES
from fieldbrief.exceptions import LLMCallError
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.rate_limit import ProviderRateLimiter

logger = logging.getLogger("fieldbrief.llm.gateway")

T = TypeVar("T", bound=BaseModel)


class Task(StrEnum):
    EXTRACT = "extract"
    VERIFY = "verify"
    SYNTHESIZE = "synthesize"


TASK_TIER: dict[Task, str] = {
    Task.EXTRACT: "cheap",
    Task.VERIFY: "cheap",
    Task.SYNTHESIZE: "strong",
}


class LLMGateway:
    def __init__(self, rate_limiter: ProviderRateLimiter | None = None) -> None:
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
            num_retries=EXTRACT_NUM_RETRIES,
        )
        raw = response.choices[0].message.content
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
            num_retries=REPAIR_NUM_RETRIES,
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


_CHARS_PER_TOKEN_ESTIMATE = 4
_COMPLETION_TO_PROMPT_RATIO_ESTIMATE = 3


def _estimate_cost(model: str, messages: list[dict[str, str]]) -> float:
    char_count = sum(len(m.get("content", "")) for m in messages)
    estimated_prompt_tokens = max(1, char_count // _CHARS_PER_TOKEN_ESTIMATE)
    pricing = litellm.model_cost.get(model, {})
    input_cost_per_token = float(pricing.get("input_cost_per_token", 0.0))
    output_cost_per_token = float(pricing.get("output_cost_per_token", 0.0))
    estimated_completion_tokens = estimated_prompt_tokens // _COMPLETION_TO_PROMPT_RATIO_ESTIMATE
    return (
        estimated_prompt_tokens * input_cost_per_token
        + estimated_completion_tokens * output_cost_per_token
    )
