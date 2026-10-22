from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from fieldbrief.exceptions import BudgetExceededError, LLMCallError
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway, Task


class Answer(BaseModel):
    value: str


def _fake_response(
    content: str, prompt_tokens: int = 10, completion_tokens: int = 5
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


@pytest.mark.asyncio
async def test_call_returns_parsed_schema_on_valid_json() -> None:
    gateway = LLMGateway()
    budget = Budget(limit_usd=1.0)
    response = _fake_response('{"value": "hello"}')

    with (
        patch("fieldbrief.llm.gateway.litellm.acompletion", new=AsyncMock(return_value=response)),
        patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
    ):
        result = await gateway.call(
            Task.EXTRACT, [{"role": "user", "content": "hi"}], Answer, budget
        )

    assert result == Answer(value="hello")
    assert budget.spent_usd == pytest.approx(0.001)


@pytest.mark.asyncio
async def test_call_acquires_rate_limit_before_calling_llm() -> None:
    gateway = LLMGateway()
    gateway._rate_limiter.acquire = AsyncMock()  # type: ignore[method-assign]
    budget = Budget(limit_usd=1.0)
    response = _fake_response('{"value": "hello"}')

    with (
        patch("fieldbrief.llm.gateway.litellm.acompletion", new=AsyncMock(return_value=response)),
        patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
    ):
        await gateway.call(Task.EXTRACT, [{"role": "user", "content": "hi"}], Answer, budget)

    gateway._rate_limiter.acquire.assert_awaited_once_with("gemini")


@pytest.mark.asyncio
async def test_call_routes_extract_to_cheap_and_synthesize_to_strong() -> None:
    gateway = LLMGateway()
    assert gateway.model_for(Task.EXTRACT) == gateway.model_for(Task.VERIFY)
    assert gateway.model_for(Task.EXTRACT) != gateway.model_for(Task.SYNTHESIZE)


@pytest.mark.asyncio
async def test_call_repairs_invalid_json_once() -> None:
    gateway = LLMGateway()
    budget = Budget(limit_usd=1.0)
    bad = _fake_response("not json")
    good = _fake_response('{"value": "fixed"}')
    mock_call = AsyncMock(side_effect=[bad, good])

    with (
        patch("fieldbrief.llm.gateway.litellm.acompletion", new=mock_call),
        patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.0005),
    ):
        result = await gateway.call(
            Task.EXTRACT, [{"role": "user", "content": "hi"}], Answer, budget
        )

    assert result == Answer(value="fixed")
    assert mock_call.call_count == 2


@pytest.mark.asyncio
async def test_call_raises_when_repair_also_invalid() -> None:
    gateway = LLMGateway()
    budget = Budget(limit_usd=1.0)
    bad = _fake_response("not json")
    still_bad = _fake_response("still not json")
    mock_call = AsyncMock(side_effect=[bad, still_bad])

    with (
        patch("fieldbrief.llm.gateway.litellm.acompletion", new=mock_call),
        patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
        pytest.raises(LLMCallError),
    ):
        await gateway.call(Task.EXTRACT, [{"role": "user", "content": "hi"}], Answer, budget)


@pytest.mark.asyncio
async def test_repair_call_cost_is_settled_against_the_budget() -> None:
    """A repair call is a second real provider call — it used to be neither
    budget-checked nor settled, making it effectively free and uncapped."""
    gateway = LLMGateway()
    budget = Budget(limit_usd=1.0)
    bad = _fake_response("not json")
    good = _fake_response('{"value": "fixed"}')
    mock_call = AsyncMock(side_effect=[bad, good])

    with (
        patch("fieldbrief.llm.gateway.litellm.acompletion", new=mock_call),
        patch("fieldbrief.llm.gateway.litellm.completion_cost", side_effect=[0.001, 0.002]),
    ):
        await gateway.call(Task.EXTRACT, [{"role": "user", "content": "hi"}], Answer, budget)

    assert budget.spent_usd == pytest.approx(0.003)


@pytest.mark.asyncio
async def test_call_raises_budget_exceeded_before_calling_llm() -> None:
    gateway = LLMGateway()
    budget = Budget(limit_usd=0.00001)  # tiny — a real gemini price estimate exceeds it
    mock_call = AsyncMock()

    with (
        patch("fieldbrief.llm.gateway.litellm.acompletion", new=mock_call),
        pytest.raises(BudgetExceededError),
    ):
        await gateway.call(
            Task.EXTRACT,
            [{"role": "user", "content": "x" * 500}],
            Answer,
            budget,
        )

    mock_call.assert_not_called()


@pytest.mark.asyncio
async def test_call_releases_the_reservation_when_the_llm_call_fails() -> None:
    gateway = LLMGateway()
    budget = Budget(limit_usd=1.0)
    mock_call = AsyncMock(side_effect=RuntimeError("provider outage"))

    with (
        patch("fieldbrief.llm.gateway.litellm.acompletion", new=mock_call),
        pytest.raises(RuntimeError),
    ):
        await gateway.call(Task.EXTRACT, [{"role": "user", "content": "hi"}], Answer, budget)

    assert budget.spent_usd == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_deterministic_calls_are_cached() -> None:
    gateway = LLMGateway()
    budget = Budget(limit_usd=1.0)
    response = _fake_response('{"value": "cached"}')
    mock_call = AsyncMock(return_value=response)

    with (
        patch("fieldbrief.llm.gateway.litellm.acompletion", new=mock_call),
        patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
    ):
        messages = [{"role": "user", "content": "same every time"}]
        first = await gateway.call(Task.EXTRACT, messages, Answer, budget, deterministic=True)
        second = await gateway.call(Task.EXTRACT, messages, Answer, budget, deterministic=True)

    assert first == second == Answer(value="cached")
    assert mock_call.call_count == 1
    assert budget.spent_usd == pytest.approx(0.001)  # only charged once
