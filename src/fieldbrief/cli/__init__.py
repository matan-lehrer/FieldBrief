import asyncio
import json

import httpx
import typer

from fieldbrief.agents.field_checklist import (
    TIER_COMPETITOR_COUNT,
    Tier,
    budget_usd_for,
    time_budget_seconds_for,
)
from fieldbrief.agents.orchestrator import run_report
from fieldbrief.config import settings
from fieldbrief.db.repositories import create_report, get_or_create_cli_user, get_or_create_company
from fieldbrief.db.session import SessionFactory
from fieldbrief.fetch.clean import clean_html, content_hash
from fieldbrief.fetch.http import DEFAULT_USER_AGENT, Fetcher
from fieldbrief.fetch.wikidata import get_company_details, pick_company_like, search_candidates
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway

app = typer.Typer(
    name="fieldbrief",
    help="AI-powered competitive analysis, built on real strategy frameworks.",
    no_args_is_help=True,
)


@app.command()
def analyze(
    company: str = typer.Argument(..., help="Company name to analyze, e.g. 'Wix'"),
    tier: Tier = typer.Option("standard", "--tier", help="quick, standard, or deep"),
    competitors: int = typer.Option(
        -1, "--competitors", help="Number of competitors (default: the tier's own count)"
    ),
) -> None:
    """Analyze a company and produce a cited strategy report."""
    competitor_count = competitors if competitors >= 0 else TIER_COMPETITOR_COUNT[tier]
    asyncio.run(_analyze(company, tier, competitor_count, budget_usd_for(tier)))


async def _analyze(company_name: str, tier: Tier, competitor_count: int, budget_usd: float) -> None:
    async with httpx.AsyncClient(headers={"User-Agent": DEFAULT_USER_AGENT}) as wikidata_client:
        candidates = await search_candidates(wikidata_client, company_name, limit=5)
        if not candidates:
            typer.echo(f"No Wikidata candidates found for '{company_name}'.")
            raise typer.Exit(code=1)
        best = await pick_company_like(wikidata_client, candidates) or candidates[0]
        details = await get_company_details(wikidata_client, best.qid)

        typer.echo(f"Resolved to: {details.name} ({details.qid})")

        budget = Budget(limit_usd=budget_usd)
        gateway = LLMGateway()
        fetcher = Fetcher()
        http_client = httpx.AsyncClient()
        edgar_client = httpx.AsyncClient(headers={"User-Agent": settings.sec_user_agent})

        try:
            async with SessionFactory() as session:
                user = await get_or_create_cli_user(session)
                company = await get_or_create_company(
                    session,
                    name=details.name,
                    domain=details.domain,
                    aliases=details.aliases,
                    ticker=details.ticker,
                    cik=details.cik,
                    industry=details.industry,
                )
                report = await create_report(
                    session, user_id=user.id, target_company_id=company.id, tier=tier
                )
                await session.commit()

                typer.echo(
                    f"Report {report.id} started (tier={tier}, competitors={competitor_count}, "
                    f"budget=${budget.limit_usd:.2f})..."
                )

                report_json = await run_report(
                    session=session,
                    gateway=gateway,
                    budget=budget,
                    fetcher=fetcher,
                    http_client=http_client,
                    wikidata_client=wikidata_client,
                    searxng_url=settings.searxng_url,
                    report_id=report.id,
                    target_company=company,
                    tier=tier,
                    competitor_count=competitor_count,
                    edgar_client=edgar_client,
                    time_budget_seconds=time_budget_seconds_for(tier),
                )
        finally:
            await fetcher.aclose()
            await http_client.aclose()
            await edgar_client.aclose()

    typer.echo(f"\nDone. Cost: ${budget.spent_usd:.4f} / ${budget.limit_usd:.2f}")
    typer.echo(json.dumps(report_json, indent=2))


@app.command()
def resolve(query: str = typer.Argument(..., help="Company name to resolve, e.g. 'Wix'")) -> None:
    """Resolve a company name via Wikidata and fetch a clean source from its site.

    Stage 2 slice: no database writes yet (that starts in stage 3) — this just
    proves entity resolution and the fetch/clean pipeline work end to end.
    """
    asyncio.run(_resolve(query))


async def _resolve(query: str) -> None:
    async with httpx.AsyncClient(headers={"User-Agent": DEFAULT_USER_AGENT}) as client:
        candidates = await search_candidates(client, query, limit=5)
        if not candidates:
            typer.echo(f"No Wikidata candidates found for '{query}'.")
            raise typer.Exit(code=1)

        typer.echo(f"Candidates for '{query}':")
        for c in candidates:
            typer.echo(f"  {c.qid}  {c.label} — {c.description or '(no description)'}")

        best = await pick_company_like(client, candidates)
        if best is None:
            typer.echo(
                f"\nNone of the candidates for '{query}' look like a business "
                "(no business/enterprise/company P31) — picking the top name match, "
                "but this is a guess."
            )
            best = candidates[0]
        details = await get_company_details(client, best.qid)
        typer.echo(f"\nResolved to: {details.name} ({details.qid})")
        typer.echo(f"  aliases:  {details.aliases}")
        typer.echo(f"  domain:   {details.domain}")
        typer.echo(f"  ticker:   {details.ticker}")
        typer.echo(f"  industry: {details.industry}")
        typer.echo(f"  cik:      {details.cik or '(not found)'}")

        if not details.domain:
            typer.echo("\nNo domain to fetch — stopping here.")
            return

    fetcher = Fetcher()
    try:
        typer.echo(f"\nFetching {details.domain} ...")
        html = await fetcher.fetch(details.domain)
        if html is None:
            typer.echo("Fetch failed or was blocked by robots.txt.")
            raise typer.Exit(code=1)
        cleaned = clean_html(html)
        if cleaned is None:
            typer.echo("Page fetched but too thin to extract — treating as not found.")
            raise typer.Exit(code=1)
        typer.echo(
            f"Cleaned text: {len(cleaned)} chars, content_hash={content_hash(cleaned)[:16]}..."
        )
        typer.echo(f"\n{cleaned[:400]}")
    finally:
        await fetcher.aclose()


@app.command()
def version() -> None:
    """Print the installed fieldbrief version."""
    from importlib.metadata import version as pkg_version

    typer.echo(pkg_version("fieldbrief"))


if __name__ == "__main__":
    app()
