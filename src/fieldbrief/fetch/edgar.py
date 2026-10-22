import httpx
from pydantic import BaseModel

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{document}"

ANNUAL_REPORT_FORMS = {"10-K", "20-F", "40-F"}


class Filing(BaseModel):
    form: str
    filed_at: str
    accession_number: str
    document_url: str


async def get_recent_annual_filings(
    client: httpx.AsyncClient, cik: str, limit: int = 3
) -> list[Filing]:
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
