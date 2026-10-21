import time

import pytest

from fieldbrief.llm.rate_limit import ProviderRateLimiter, TokenBucket


@pytest.mark.asyncio
async def test_bucket_allows_burst_up_to_capacity() -> None:
    bucket = TokenBucket(rate_per_s=1.0, capacity=3.0)
    start = time.monotonic()
    for _ in range(3):
        await bucket.acquire()
    assert time.monotonic() - start < 0.1  # all within the initial burst, no waiting


@pytest.mark.asyncio
async def test_bucket_waits_once_capacity_exhausted() -> None:
    bucket = TokenBucket(rate_per_s=20.0, capacity=1.0)
    await bucket.acquire()  # drains the single token
    start = time.monotonic()
    await bucket.acquire()  # must wait ~1/20s for a refill
    assert time.monotonic() - start >= 0.03


@pytest.mark.asyncio
async def test_provider_rate_limiter_isolates_providers() -> None:
    limiter = ProviderRateLimiter(rate_per_s=1.0, capacity=1.0)
    await limiter.acquire("gemini")
    start = time.monotonic()
    await limiter.acquire("openai")  # different provider, its own fresh bucket
    assert time.monotonic() - start < 0.1
