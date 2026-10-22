import httpx
import pytest
import respx

from fieldbrief.fetch.edgar import SUBMISSIONS_URL, get_recent_annual_filings

SUBMISSIONS_RESPONSE = {
    "name": "Wix.com Ltd.",
    "filings": {
        "recent": {
            "form": ["6-K", "20-F", "6-K", "20-F"],
            "filingDate": ["2026-06-01", "2026-03-05", "2025-09-01", "2025-03-21"],
            "accessionNumber": [
                "0001-26-000001",
                "0001628280-26-015222",
                "0001-25-000002",
                "0001628280-25-014212",
            ],
            "primaryDocument": ["a.htm", "wix-20251231.htm", "b.htm", "wix-20241231.htm"],
        }
    },
}


@pytest.mark.asyncio
@respx.mock
async def test_get_recent_annual_filings_filters_to_annual_forms() -> None:
    url = SUBMISSIONS_URL.format(cik=1576789)
    respx.get(url).mock(return_value=httpx.Response(200, json=SUBMISSIONS_RESPONSE))

    async with httpx.AsyncClient() as client:
        filings = await get_recent_annual_filings(client, "1576789", limit=3)

    assert [f.form for f in filings] == ["20-F", "20-F"]
    assert filings[0].filed_at == "2026-03-05"
    assert filings[0].document_url == (
        "https://www.sec.gov/Archives/edgar/data/1576789/000162828026015222/wix-20251231.htm"
    )
