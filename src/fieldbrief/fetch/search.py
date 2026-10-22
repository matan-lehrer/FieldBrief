import httpx
from pydantic import BaseModel


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str | None = None


async def search(
    client: httpx.AsyncClient, searxng_url: str, query: str, limit: int = 10
) -> list[SearchResult]:
    response = await client.get(
        f"{searxng_url.rstrip('/')}/search",
        params={"q": query, "format": "json"},
    )
    response.raise_for_status()
    data = response.json()
    results = []
    for item in data.get("results", [])[:limit]:
        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=item["url"],
                snippet=item.get("content"),
            )
        )
    return results
