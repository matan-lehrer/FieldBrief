import httpx
import pytest
import respx

from fieldbrief.fetch.wikidata import (
    WIKIDATA_API,
    CompanyCandidate,
    get_company_details,
    pick_company_like,
    search_candidates,
)


def _instance_of(qid: str) -> dict:
    return {"mainsnak": {"datavalue": {"value": {"id": qid}}}}


def _string_claim(value: str) -> dict:
    return {"mainsnak": {"datavalue": {"value": value}}}


def _exchange_claim_with_ticker(ticker: str) -> dict:
    return {
        "mainsnak": {"datavalue": {"value": {"id": "Q82059"}}},
        "qualifiers": {"P249": [{"datavalue": {"value": ticker}}]},
    }


SEARCH_RESPONSE = {
    "search": [
        {"id": "Q1839789", "label": "Wix", "description": "village and civil parish in Essex"},
        {"id": "Q420506", "label": "Wix.com", "description": "Israeli online services company"},
    ]
}

WIX_COMPANY_CLAIMS = {
    "P31": [_instance_of("Q4830453"), _instance_of("Q891723")],
    "P856": [_string_claim("https://wix.com/")],
    "P414": [_exchange_claim_with_ticker("WIX")],
    "P452": [_instance_of("Q1625990")],
}

WIX_VILLAGE_CLAIMS = {"P31": [_instance_of("Q532")]}


@pytest.mark.asyncio
@respx.mock
async def test_search_candidates_returns_parsed_candidates() -> None:
    respx.get(WIKIDATA_API).mock(return_value=httpx.Response(200, json=SEARCH_RESPONSE))

    async with httpx.AsyncClient() as client:
        candidates = await search_candidates(client, "Wix")

    assert [c.qid for c in candidates] == ["Q1839789", "Q420506"]
    assert candidates[1].description == "Israeli online services company"


@pytest.mark.asyncio
@respx.mock
async def test_pick_company_like_skips_non_business_entities() -> None:
    claims_response = {
        "entities": {
            "Q1839789": {"claims": WIX_VILLAGE_CLAIMS},
            "Q420506": {"claims": WIX_COMPANY_CLAIMS},
        }
    }
    respx.get(WIKIDATA_API).mock(return_value=httpx.Response(200, json=claims_response))

    candidates = [
        CompanyCandidate(qid="Q1839789", label="Wix", description="village"),
        CompanyCandidate(qid="Q420506", label="Wix.com", description="company"),
    ]

    async with httpx.AsyncClient() as client:
        best = await pick_company_like(client, candidates)

    assert best is not None
    assert best.qid == "Q420506"


@pytest.mark.asyncio
@respx.mock
async def test_pick_company_like_returns_none_when_nothing_matches() -> None:
    claims_response = {"entities": {"Q1839789": {"claims": WIX_VILLAGE_CLAIMS}}}
    respx.get(WIKIDATA_API).mock(return_value=httpx.Response(200, json=claims_response))

    candidates = [CompanyCandidate(qid="Q1839789", label="Wix", description="village")]

    async with httpx.AsyncClient() as client:
        best = await pick_company_like(client, candidates)

    assert best is None


@pytest.mark.asyncio
@respx.mock
async def test_get_company_details_parses_website_ticker_industry() -> None:
    entity_response = {
        "entities": {
            "Q420506": {
                "labels": {"en": {"value": "Wix.com"}},
                "aliases": {"en": [{"value": "Wix"}]},
                "claims": WIX_COMPANY_CLAIMS,
            }
        }
    }
    industry_label_response = {
        "entities": {"Q1625990": {"labels": {"en": {"value": "website builder"}}}}
    }

    route = respx.get(WIKIDATA_API)
    route.side_effect = [
        httpx.Response(200, json=entity_response),
        httpx.Response(200, json=industry_label_response),
    ]

    async with httpx.AsyncClient() as client:
        details = await get_company_details(client, "Q420506")

    assert details.name == "Wix.com"
    assert details.aliases == ["Wix"]
    assert details.domain == "https://wix.com/"
    assert details.ticker == "WIX"
    assert details.industry == "website builder"
    assert details.cik is None
