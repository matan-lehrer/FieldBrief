"""Per-provider rate limiting for LLM calls.

design.md "LLM gateway" step 4: "Rate limit: token bucket per provider
(in-process with one worker, Postgres-backed with several); wait, don't fail."
MVP runs one worker, so in-process is the whole requirement — a shared,
Postgres-backed bucket is scale-path work (design.md §10), not MVP.
"""

import asyncio
import time


class TokenBucket:
    """Classic token bucket: `rate` tokens/second, up to `capacity` burst."""

    def __init__(self, rate_per_s: float, capacity: float) -> None:
        self._rate = rate_per_s
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._last_refill = now
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                wait_s = (cost - self._tokens) / self._rate
                await asyncio.sleep(wait_s)


class ProviderRateLimiter:
    """One TokenBucket per provider name (e.g. "gemini"), created on first use."""

    def __init__(self, rate_per_s: float = 10.0, capacity: float = 10.0) -> None:
        self._rate_per_s = rate_per_s
        self._capacity = capacity
        self._buckets: dict[str, TokenBucket] = {}

    async def acquire(self, provider: str) -> None:
        bucket = self._buckets.setdefault(provider, TokenBucket(self._rate_per_s, self._capacity))
        await bucket.acquire()
