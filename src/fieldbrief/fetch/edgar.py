"""SEC EDGAR client: ticker -> CIK -> recent filings -> filing document URL.

design.md §9: "EDGAR with a declared User-Agent." SEC requires a User-Agent
shaped like "Company Name contact@domain" — specifically an email-looking
string, not just a descriptive name or URL — or every request gets a 403.
Always pass a client whose User-Agent is settings.sec_user_agent, never the
generic fetch layer's DEFAULT_USER_AGENT (verified live 2026-10-21: a
perfectly descriptive but email-less UA was rejected; the email-shaped one
wasn't).

Verified live (2026-10-21) against Wix.com (CIK 1576789, foreign private
issuer -> files 20-F, not 10-K).
"""

import httpx
from pydantic import BaseModel

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{document}"

# Annual-report forms across both domestic filers and foreign private issuers
# (field-checklist.md's `risks.disclosed` / `financials.*` want the latest one).
ANNUAL_REPORT_FORMS = {"10-K", "20-F", "40-F"}


class Filing(BaseModel):
    form: str
    filed_at: str
    accession_number: str
    document_url: str


async def lookup_cik_by_ticker(client: httpx.AsyncClient, ticker: str) -> str | None:
    """SEC publishes the whole ticker->CIK map as one static file — no per-ticker endpoint."""
    response = await client.get(TICKERS_URL)
    response.raise_for_status()
    data = response.json()
    ticker_upper = ticker.upper()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            return str(entry["cik_str"])
    return None


async def get_recent_annual_filings(
    client: httpx.AsyncClient, cik: str, limit: int = 3
) -> list[Filing]:
    """Most recent 10-K/20-F/40-F filings, newest first."""
    url = SUBMISSIONS_URL.format(cik=int(cik))
    response = await client.get(url)
    response.raise_for_status()
    data = response.json()
    recent = data["filings"]["recent"]

    filings: list[Filing] = []
    for form, filed_at, accession, document in zip(
        recent["form"],
        recent["filingDate"],
        recent["accessionNumber"],
        recent["primaryDocument"],
        strict=True,
    ):
        if form not in ANNUAL_REPORT_FORMS:
            continue
        accession_no_dashes = accession.replace("-", "")
        filings.append(
            Filing(
                form=form,
                filed_at=filed_at,
                accession_number=accession,
                document_url=ARCHIVES_URL.format(
                    cik=int(cik), accession_no_dashes=accession_no_dashes, document=document
                ),
            )
        )
        if len(filings) >= limit:
            break
    return filings
