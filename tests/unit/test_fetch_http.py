import httpx
import pytest
import respx

from fieldbrief.fetch.http import Fetcher

PAGE_URL = "https://example.com/about"
ROBOTS_URL = "https://example.com/robots.txt"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_returns_text_when_allowed() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n"))
    respx.get(PAGE_URL).mock(
        return_value=httpx.Response(
            200, text="<html>hi</html>", headers={"content-type": "text/html"}
        )
    )

    fetcher = Fetcher(min_interval_per_domain_s=0)
    try:
        result = await fetcher.fetch(PAGE_URL)
    finally:
        await fetcher.aclose()

    assert result == "<html>hi</html>"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_returns_none_when_robots_disallows() -> None:
    respx.get(ROBOTS_URL).mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /about\n")
    )

    fetcher = Fetcher(min_interval_per_domain_s=0)
    try:
        result = await fetcher.fetch(PAGE_URL)
    finally:
        await fetcher.aclose()

    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_fails_open_when_robots_txt_unreachable() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    respx.get(PAGE_URL).mock(
        return_value=httpx.Response(
            200, text="<html>hi</html>", headers={"content-type": "text/html"}
        )
    )

    fetcher = Fetcher(min_interval_per_domain_s=0)
    try:
        result = await fetcher.fetch(PAGE_URL)
    finally:
        await fetcher.aclose()

    assert result == "<html>hi</html>"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_returns_none_for_non_html_content_type() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n"))
    respx.get(PAGE_URL).mock(
        return_value=httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
    )

    fetcher = Fetcher(min_interval_per_domain_s=0)
    try:
        result = await fetcher.fetch(PAGE_URL)
    finally:
        await fetcher.aclose()

    assert result is None
