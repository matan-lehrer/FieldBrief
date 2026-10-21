"""Per-company research nodes (ADR 0010's inner, per-field fan-out level).
Each node is `async def node(state, **deps) -> dict` — a partial state update,
per LangGraph convention. Dependencies (DB session, LLM gateway, budget,
fetcher, HTTP client) are injected by agents/graph.py via functools.partial,
not carried in state (state is data, per ADR 0001).

analyze_node builds CompanySnapshot deterministically (see its docstring);
everything cross-company (Five Forces, BMC, VRIO, positioning, SWOT,
recommendations) lives in agents/synthesis.py, run once after every company's
analyze_node completes (agents/orchestrator.py).
"""

import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.agents import prompts
from fieldbrief.agents.field_checklist import MAX_RETRY_ROUNDS as TIER_MAX_RETRY_ROUNDS
from fieldbrief.agents.field_checklist import Tier, fields_for
from fieldbrief.agents.llm_schemas import ClaimVerification, ExtractionResult
from fieldbrief.agents.state import GraphState
from fieldbrief.db.models import Claim, Source
from fieldbrief.db.repositories import add_claim, get_fresh_claims, get_or_create_source
from fieldbrief.fetch.clean import clean_html, content_hash
from fieldbrief.fetch.http import Fetcher
from fieldbrief.fetch.search import search
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway, Task
from fieldbrief.reports.confidence import compute_citation

MAX_ATTEMPTS_PER_FIELD = 2

FRESHNESS_MAX_AGE: dict[str, timedelta] = {
    "events.recent": timedelta(days=7),
    "financials.revenue": timedelta(days=365),
}
DEFAULT_FRESHNESS_MAX_AGE = timedelta(days=30)

# Fields with no dedicated search query share the overview/domain fetch
# (field-checklist.md: most Quick-tier fields and offering.value_prop are
# typically covered by a company's own "about" page). Everything else gets
# its own targeted search when it's still a gap after the overview.
SEARCH_GROUPS: list[tuple[set[str], str]] = [
    ({"events.recent"}, "{name} news"),
    ({"pricing.model"}, "{name} pricing"),
    ({"financials.profitability", "financials.funding_or_cap"}, "{name} funding financials"),
    ({"market.forces", "resources.advantages"}, "{name} competitive advantage analysis"),
    ({"environment.regulation"}, "{name} regulation compliance"),
]
RESULTS_PER_SEARCH = 4  # enough headroom for a few retry rounds to each see a fresh result


def _max_age(field: str) -> timedelta:
    return FRESHNESS_MAX_AGE.get(field, DEFAULT_FRESHNESS_MAX_AGE)


def _source_type(url: str, company_domain: str | None) -> str:
    if company_domain and urlparse(url).netloc == urlparse(company_domain).netloc:
        return "official"
    return "news"


async def plan_node(state: GraphState, *, session: AsyncSession) -> dict[str, Any]:
    company_id = uuid.UUID(state["company_id"])
    fields = fields_for(state["tier"], state["is_target"])
    gaps = []
    for field in fields:
        fresh = await get_fresh_claims(session, company_id, field, _max_age(field))
        if not any(c.verified for c in fresh):
            gaps.append({"field": field, "attempts": 0})
    return {"gaps": gaps, "retry_round": 0}


async def research_node(
    state: GraphState,
    *,
    session: AsyncSession,
    gateway: LLMGateway,
    budget: Budget,
    fetcher: Fetcher,
    http_client: httpx.AsyncClient,
    searxng_url: str,
) -> dict[str, Any]:
    """One research round. The overview/domain page covers most fields in one
    extraction call (design.md §9: "fetch -> extract claims ... for all known
    companies on the page" — not a per-field fetch loop); fields that need
    their own angle (pricing, funding, regulation, ...) get one targeted
    search each, only when they're still a gap. Each retry round picks a
    different search result (round N -> results[N]) so looping again actually
    sees new sources instead of re-fetching (and skipping, via content_hash
    dedup) the same page."""
    if not state["gaps"]:
        return {}

    company_id = uuid.UUID(state["company_id"])
    company_name = state["company_name"]
    company_domain = state["company_domain"]
    gap_fields = {g["field"] for g in state["gaps"]}
    round_index = state["retry_round"]
    claim_ids = list(state["claim_ids"])

    urls: list[str] = []
    if company_domain:
        urls.append(company_domain)
    else:
        results = await search(
            http_client, searxng_url, f"{company_name} company overview", limit=RESULTS_PER_SEARCH
        )
        if results:
            urls.append(results[min(round_index, len(results) - 1)].url)

    for group_fields, query_template in SEARCH_GROUPS:
        if not (group_fields & gap_fields):
            continue
        results = await search(
            http_client,
            searxng_url,
            query_template.format(name=company_name),
            limit=RESULTS_PER_SEARCH,
        )
        if results:
            urls.append(results[min(round_index, len(results) - 1)].url)

    for url in urls:
        html = await fetcher.fetch(url)
        if html is None:
            continue
        cleaned = clean_html(html)
        if cleaned is None:
            continue

        source, changed = await get_or_create_source(
            session,
            url=url,
            content_hash=content_hash(cleaned),
            source_type=_source_type(url, company_domain),
        )
        if not changed:
            continue  # same content already extracted previously — skip re-extraction

        extraction = await gateway.call(
            Task.EXTRACT,
            prompts.extraction_prompt(company_name, cleaned, sorted(gap_fields)),
            ExtractionResult,
            budget,
            report_id=state["report_id"],
            stage="research",
        )
        for extracted in extraction.claims:
            snippet = cleaned[:500]
            value = dict(extracted.value)
            if extracted.bmc_block:
                value["bmc_block"] = extracted.bmc_block
            claim = await add_claim(
                session,
                company_id=company_id,
                source_id=source.id,
                field=extracted.field,
                statement=extracted.statement,
                value=value,
                snippet=snippet,
                relevance=extracted.relevance,
            )
            verification = await gateway.call(
                Task.VERIFY,
                prompts.verification_prompt(extracted.statement, snippet),
                ClaimVerification,
                budget,
                report_id=state["report_id"],
                stage="verify",
            )
            claim.verified = verification.verified
            await session.flush()
            claim_ids.append(str(claim.id))

    return {"claim_ids": claim_ids}


async def gap_check_node(state: GraphState, *, session: AsyncSession) -> dict[str, Any]:
    company_id = uuid.UUID(state["company_id"])
    remaining = []
    for gap in state["gaps"]:
        fresh = await get_fresh_claims(session, company_id, gap["field"], _max_age(gap["field"]))
        if any(c.verified for c in fresh):
            continue  # resolved — drop from gaps
        remaining.append({"field": gap["field"], "attempts": gap["attempts"] + 1})
    return {"gaps": remaining, "retry_round": state["retry_round"] + 1}


def should_continue_researching(state: GraphState, *, tier: Tier) -> str:
    max_rounds = TIER_MAX_RETRY_ROUNDS[tier]
    if state["gaps"] and state["retry_round"] < max_rounds:
        return "loop"
    return "advance"


async def analyze_node(state: GraphState, *, session: AsyncSession) -> dict[str, Any]:
    """Deterministic CompanySnapshot assembly — grouping already-extracted,
    already-tagged claims by field is data wrangling, not judgment, so no LLM
    call here (cheaper, and nothing to hallucinate). The real judgment calls
    (Five Forces, VRIO, positioning, SWOT, recommendations) happen once,
    across all companies, in agents/synthesis.py after every company's
    analyze_node has run."""
    company_id = uuid.UUID(state["company_id"])

    claims_result = await session.execute(
        select(Claim)
        .where(Claim.company_id == company_id, Claim.verified.is_(True))
        .order_by(Claim.extracted_at)
    )
    claims = list(claims_result.scalars().all())

    source_ids = {c.source_id for c in claims}
    sources: dict[uuid.UUID, Source] = {}
    if source_ids:
        sources_result = await session.execute(select(Source).where(Source.id.in_(source_ids)))
        sources = {s.id: s for s in sources_result.scalars().all()}

    by_field: dict[str, list[Claim]] = defaultdict(list)
    for claim in claims:
        by_field[claim.field].append(claim)

    def citation_for(field: str) -> dict[str, Any] | None:
        field_claims = by_field.get(field)
        if not field_claims:
            return None
        return compute_citation(field_claims, sources).model_dump(mode="json")

    snapshot = {
        "company_id": str(company_id),
        "name": state["company_name"],
        "is_target": state["is_target"],
        "description": citation_for("profile.description"),
        "products": citation_for("offering.products"),
        "customer_segments": citation_for("customers.segments"),
        "revenue_model": citation_for("revenue.model"),
        "revenue": citation_for("financials.revenue"),
        "profitability": citation_for("financials.profitability"),
        "funding_or_cap": citation_for("financials.funding_or_cap"),
        "recent_events": citation_for("events.recent"),
        "freshness": {"as_of": date.today().isoformat(), "stale": False},
    }
    return {"company_snapshot": snapshot}
