"""Company entity resolution via Wikidata.

Property IDs used here:
- P856  official website
- P452  industry (value is an item; resolved to a label with a second lookup)
- P414  stock exchange, qualified by P249 (ticker symbol) — the ticker is a
        *qualifier* on the exchange claim, not a top-level property.
- P5531 SEC Central Index Key (CIK) — present on SEC filers, absent on many
        others. Missing is a legitimate "not found", not a bug.
"""

from typing import Any

import httpx
from pydantic import BaseModel

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# P31 (instance of) values that mark a candidate as business-like, not a
# place/person/other entity that happens to share a label (ADR 0011).
COMPANY_LIKE_CLASSES = {
    "Q4830453",  # business
    "Q6881511",  # enterprise
    "Q891723",  # public company
    "Q783794",  # company
}


class CompanyCandidate(BaseModel):
    qid: str
    label: str
    description: str | None = None


class CompanyDetails(BaseModel):
    qid: str
    name: str
    aliases: list[str] = []
    domain: str | None = None
    ticker: str | None = None
    cik: str | None = None
    industry: str | None = None


async def search_candidates(
    client: httpx.AsyncClient, query: str, limit: int = 5
) -> list[CompanyCandidate]:
    """Raw name search — multiple unrelated entities can share a label (ADR 0011's
    Wix/Wiz case), so this returns candidates for the caller/user to disambiguate,
    not a single resolved answer."""
    response = await client.get(
        WIKIDATA_API,
        params={
            "action": "wbsearchentities",
            "search": query,
            "language": "en",
            "format": "json",
            "type": "item",
            "limit": limit,
        },
    )
    response.raise_for_status()
    data = response.json()
    return [
        CompanyCandidate(qid=item["id"], label=item["label"], description=item.get("description"))
        for item in data.get("search", [])
    ]


async def pick_company_like(
    client: httpx.AsyncClient, candidates: list[CompanyCandidate]
) -> CompanyCandidate | None:
    """Among name-search candidates, prefer one whose P31 (instance of) marks it
    as a business — plain label ranking alone can't tell a company from a village,
    a surname, or anything else that happens to share its name (ADR 0011)."""
    if not candidates:
        return None
    ids = "|".join(c.qid for c in candidates)
    response = await client.get(
        WIKIDATA_API,
        params={"action": "wbgetentities", "ids": ids, "props": "claims", "format": "json"},
    )
    response.raise_for_status()
    entities = response.json()["entities"]

    for candidate in candidates:
        claims = entities.get(candidate.qid, {}).get("claims", {})
        instance_of = _entity_ids(claims, "P31")
        if instance_of & COMPANY_LIKE_CLASSES:
            return candidate
    return None


async def get_company_details(client: httpx.AsyncClient, qid: str) -> CompanyDetails:
    """Fetch aliases + the handful of claims the field checklist needs. Any field
    this entity doesn't have is left None — a legitimate "not found", not an error."""
    response = await client.get(
        WIKIDATA_API,
        params={
            "action": "wbgetentities",
            "ids": qid,
            "props": "labels|aliases|claims",
            "languages": "en",
            "format": "json",
        },
    )
    response.raise_for_status()
    entity = response.json()["entities"][qid]

    name = entity.get("labels", {}).get("en", {}).get("value", qid)
    aliases = [a["value"] for a in entity.get("aliases", {}).get("en", [])]
    claims = entity.get("claims", {})

    domain = _first_value(claims, "P856")
    cik = _first_value(claims, "P5531")

    ticker = None
    for claim in claims.get("P414", []):
        qualifiers = claim.get("qualifiers", {})
        if "P249" in qualifiers:
            ticker = qualifiers["P249"][0]["datavalue"]["value"]
            break

    industry = None
    industry_qid = _first_entity_id(claims, "P452")
    if industry_qid is not None:
        industry = await _get_label(client, industry_qid)

    return CompanyDetails(
        qid=qid,
        name=name,
        aliases=aliases,
        domain=domain,
        ticker=ticker,
        cik=cik,
        industry=industry,
    )


def _first_value(claims: dict[str, Any], prop: str) -> str | None:
    for claim in claims.get(prop, []):
        datavalue = claim.get("mainsnak", {}).get("datavalue")
        if not datavalue:
            continue
        value = datavalue.get("value")
        if isinstance(value, str):
            return value
    return None


def _first_entity_id(claims: dict[str, Any], prop: str) -> str | None:
    for claim in claims.get(prop, []):
        datavalue = claim.get("mainsnak", {}).get("datavalue")
        if not datavalue:
            continue
        value = datavalue.get("value")
        if isinstance(value, dict):
            entity_id = value.get("id")
            if isinstance(entity_id, str):
                return entity_id
    return None


def _entity_ids(claims: dict[str, Any], prop: str) -> set[str]:
    ids = set()
    for claim in claims.get(prop, []):
        datavalue = claim.get("mainsnak", {}).get("datavalue")
        if datavalue and isinstance(datavalue.get("value"), dict):
            entity_id = datavalue["value"].get("id")
            if entity_id is not None:
                ids.add(entity_id)
    return ids


async def _get_label(client: httpx.AsyncClient, qid: str) -> str | None:
    response = await client.get(
        WIKIDATA_API,
        params={
            "action": "wbgetentities",
            "ids": qid,
            "props": "labels",
            "languages": "en",
            "format": "json",
        },
    )
    response.raise_for_status()
    entity = response.json()["entities"].get(qid, {})
    label = entity.get("labels", {}).get("en", {}).get("value")
    return label if isinstance(label, str) else None
