"""Hits real Wikidata, SEC EDGAR, and a local SearXNG instance.

Skipped by default (pytest.ini_options addopts = "-m 'not live'"); run explicitly
with `pytest -m live`. Requires the compose stack up for the SearXNG test.
"""

import httpx
import pytest

from fieldbrief.config import settings
from fieldbrief.fetch.clean import clean_html
from fieldbrief.fetch.edgar import get_recent_annual_filings
from fieldbrief.fetch.http import DEFAULT_USER_AGENT, Fetcher
from fieldbrief.fetch.search import search
from fieldbrief.fetch.wikidata import get_company_details, pick_company_like, search_candidates

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_wikidata_resolves_wix_to_the_company_not_the_village() -> None:
    async with httpx.AsyncClient(headers={"User-Agent": DEFAULT_USER_AGENT}) as client:
        candidates = await search_candidates(client, "Wix", limit=5)
        best = await pick_company_like(client, candidates)
        assert best is not None
        assert best.qid == "Q420506"

        details = await get_company_details(client, best.qid)
        assert details.ticker == "WIX"
        assert details.domain is not None


@pytest.mark.asyncio
async def test_fetch_and_clean_real_page() -> None:
    fetcher = Fetcher()
    try:
        html = await fetcher.fetch("https://en.wikipedia.org/wiki/Wix.com")
        assert html is not None
        cleaned = clean_html(html)
        assert cleaned is not None
        assert len(cleaned) > 500
    finally:
        await fetcher.aclose()


@pytest.mark.asyncio
async def test_edgar_finds_wix_annual_filings() -> None:
    # SEC requires a User-Agent shaped like "Company Name contact@domain" — a
    # generic UA (even a descriptive one) gets a 403.
    async with httpx.AsyncClient(headers={"User-Agent": settings.sec_user_agent}) as client:
        filings = await get_recent_annual_filings(client, "1576789", limit=1)
        assert len(filings) == 1
        assert filings[0].form in ("10-K", "20-F", "40-F")


@pytest.mark.asyncio
async def test_searxng_local_instance_returns_results() -> None:
    async with httpx.AsyncClient() as client:
        results = await search(client, "http://localhost:8080", "Wix.com", limit=5)
    assert len(results) > 0
