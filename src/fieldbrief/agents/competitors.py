"""Competitor discovery (ADR 0012): search-grounded, not LLM-proposed."""

import httpx
from pydantic import BaseModel, Field

from fieldbrief.fetch.clean import clean_html, select_relevant_passages
from fieldbrief.fetch.http import Fetcher
from fieldbrief.fetch.search import search
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway, Task


class CompetitorCandidate(BaseModel):
    name: str
    relevance: float = Field(ge=0, le=1)


class CompetitorDiscoveryResult(BaseModel):
    competitors: list[CompetitorCandidate] = Field(default_factory=list)


def _discovery_prompt(company_name: str, page_text: str) -> list[dict[str, str]]:
    system = (
        "You read a web page and list companies it names as competitors or "
        "alternatives to the given company. Only list a company if the page "
        "actually names it as a competitor/alternative — never add one from "
        "general knowledge. If the page names none, return an empty list."
    )
    keywords = {
        *company_name.split(),
        "competitor",
        "competitors",
        "alternative",
        "alternatives",
        "rival",
        "vs",
    }
    bounded_text = select_relevant_passages(page_text, keywords, max_chars=6000)
    user = f"Company: {company_name}\n\nPage text:\n{bounded_text}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def discover_competitors(
    company_name: str,
    *,
    http_client: httpx.AsyncClient,
    fetcher: Fetcher,
    gateway: LLMGateway,
    budget: Budget,
    searxng_url: str,
    report_id: str,
    limit: int,
) -> list[CompetitorCandidate]:
    """Search-grounded only (ADR 0012) — a candidate with no page naming it as
    a competitor doesn't make the list, even if the model "knows" of one."""
    results = await search(http_client, searxng_url, f"{company_name} competitors", limit=3)
    if not results:
        return []

    html = await fetcher.fetch(results[0].url)
    if html is None:
        return []
    cleaned = clean_html(html)
    if cleaned is None:
        return []

    discovery = await gateway.call(
        Task.EXTRACT,
        _discovery_prompt(company_name, cleaned),
        CompetitorDiscoveryResult,
        budget,
        report_id=report_id,
        stage="discover_competitors",
    )
    ranked = sorted(discovery.competitors, key=lambda c: c.relevance, reverse=True)
    # Never the company itself, never duplicates by name.
    seen = {company_name.strip().lower()}
    unique: list[CompetitorCandidate] = []
    for candidate in ranked:
        key = candidate.name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique[:limit]
