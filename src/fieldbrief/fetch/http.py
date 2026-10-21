"""Shared HTTP plumbing: one client, per-domain rate limiting, robots.txt.

design.md §9 "Fetch layer": per-domain rate limits, timeouts, robots.txt respected.
"""

import asyncio
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
DEFAULT_USER_AGENT = "Fieldbrief/0.1 (+https://github.com/matan-lehrer/FieldBrief)"
MIN_INTERVAL_PER_DOMAIN_S = 1.0


class RateLimiter:
    """One token per domain, replenished every `min_interval_s` seconds."""

    def __init__(self, min_interval_s: float = MIN_INTERVAL_PER_DOMAIN_S) -> None:
        self._min_interval_s = min_interval_s
        self._last_request_at: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, domain: str) -> None:
        async with self._lock:
            last = self._last_request_at.get(domain)
            now = time.monotonic()
            if last is not None:
                elapsed = now - last
                if elapsed < self._min_interval_s:
                    await asyncio.sleep(self._min_interval_s - elapsed)
            self._last_request_at[domain] = time.monotonic()


class RobotsCache:
    """Caches parsed robots.txt per domain; fails open if robots.txt is unreachable.

    A cached value of None means "no robots.txt found, allow everything" — kept
    separate from RobotFileParser itself rather than using its undocumented
    `allow_all` attribute, which typeshed doesn't declare.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._parsers: dict[str, RobotFileParser | None] = {}

    async def allowed(self, url: str, user_agent: str = DEFAULT_USER_AGENT) -> bool:
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain not in self._parsers:
            self._parsers[domain] = await self._fetch_parser(parsed.scheme, domain)
        parser = self._parsers[domain]
        return parser is None or parser.can_fetch(user_agent, url)

    async def _fetch_parser(self, scheme: str, domain: str) -> RobotFileParser | None:
        robots_url = f"{scheme}://{domain}/robots.txt"
        try:
            response = await self._client.get(robots_url, timeout=DEFAULT_TIMEOUT)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser


class Fetcher:
    """Fetches a URL as text, respecting robots.txt and a per-domain rate limit."""

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        min_interval_per_domain_s: float = MIN_INTERVAL_PER_DOMAIN_S,
    ) -> None:
        self._user_agent = user_agent
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=DEFAULT_TIMEOUT, follow_redirects=True
        )
        self._rate_limiter = RateLimiter(min_interval_per_domain_s)
        self._robots = RobotsCache(self._client)

    async def fetch(self, url: str) -> str | None:
        """Return the raw response body, or None if blocked, unreachable, or not HTML/text."""
        domain = urlparse(url).netloc
        if not await self._robots.allowed(url, self._user_agent):
            return None
        await self._rate_limiter.wait(domain)
        try:
            response = await self._client.get(url)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        content_type = response.headers.get("content-type", "")
        if "text" not in content_type and "html" not in content_type:
            return None
        return response.text

    async def aclose(self) -> None:
        await self._client.aclose()
