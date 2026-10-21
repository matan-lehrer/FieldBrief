import httpx
import pytest
import respx

from fieldbrief.fetch.search import search

SEARXNG_URL = "http://searxng.local"

SEARCH_RESPONSE = {
    "results": [
        {
            "title": "Wix.com - Wikipedia",
            "url": "https://en.wikipedia.org/wiki/Wix.com",
            "content": "...",
        },
        {"title": "Wix homepage", "url": "https://wix.com/", "content": None},
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_search_parses_results() -> None:
    respx.get(f"{SEARXNG_URL}/search").mock(return_value=httpx.Response(200, json=SEARCH_RESPONSE))

    async with httpx.AsyncClient() as client:
        results = await search(client, SEARXNG_URL, "Wix.com", limit=10)

    assert len(results) == 2
    assert results[0].title == "Wix.com - Wikipedia"
    assert results[1].snippet is None


@pytest.mark.asyncio
@respx.mock
async def test_search_respects_limit() -> None:
    respx.get(f"{SEARXNG_URL}/search").mock(return_value=httpx.Response(200, json=SEARCH_RESPONSE))

    async with httpx.AsyncClient() as client:
        results = await search(client, SEARXNG_URL, "Wix.com", limit=1)

    assert len(results) == 1
