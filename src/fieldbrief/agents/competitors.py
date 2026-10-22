import httpx
from pydantic import BaseModel, Field

from fieldbrief.consts import COMPETITOR_DISCOVERY_MAX_CHARS
from fieldbrief.fetch.clean import clean_html, select_relevant_passages
from fieldbrief.fetch.http import Fetcher
from fieldbrief.fetch.search import search
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway, Task

COMPETITOR_SEARCH_QUERIES = ["{name} competitors", "{name} alternatives", "{name} vs"]
COMPETITOR_SEARCH_RESULTS_PER_QUERY = 4


class CompetitorCandidate(BaseModel):
    name: str
    relevance: float = Field(ge=0, le=1)


class CompetitorDiscoveryResult(BaseModel):
    competitors: list[CompetitorCandidate] = Field(default_factory=list)


def _discovery_prompt(company_name: str, pages_text: str) -> list[dict[str, str]]:
    system = (
        "You read excerpts from several web pages and list companies they "
        "name as competitors or alternatives to the given company. Only "
        "list a company if some excerpt actually names it as a "
        "competitor/alternative — never add one from general knowledge. If "
        "nothing names any, return an empty list."
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
    bounded_text = select_relevant_passages(
        pages_text, keywords, max_chars=COMPETITOR_DISCOVERY_MAX_CHARS
    )
    user = f"Company: {company_name}\n\nPage excerpts:\n{bounded_text}"
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
    seen_urls: set[str] = set()
    urls: list[str] = []
    for query_template in COMPETITOR_SEARCH_QUERIES:
        results = await search(
            http_client,
            searxng_url,
            query_template.format(name=company_name),
            limit=COMPETITOR_SEARCH_RESULTS_PER_QUERY,
        )
        for result in results:
            if result.url not in seen_urls:
                seen_urls.add(result.url)
                urls.append(result.url)
    if not urls:
        return []

    pages: list[str] = []
    for url in urls:
        html = await fetcher.fetch(url)
        if html is None:
            continue
        cleaned = clean_html(html)
        if cleaned is not None:
            pages.append(cleaned)
    if not pages:
        return []

    discovery = await gateway.call(
        Task.EXTRACT,
        _discovery_prompt(company_name, "\n\n---\n\n".join(pages)),
        CompetitorDiscoveryResult,
        budget,
        report_id=report_id,
        stage="discover_competitors",
    )
    ranked = sorted(discovery.competitors, key=lambda c: c.relevance, reverse=True)
    seen = {company_name.strip().lower()}
    unique: list[CompetitorCandidate] = []
    for candidate in ranked:
        key = candidate.name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique[:limit]
