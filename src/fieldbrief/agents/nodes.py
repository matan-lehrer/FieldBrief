"""Graph nodes (ADR 0010). Each node is `async def node(state, **deps) -> dict`
— a partial state update, per LangGraph convention. Dependencies (DB session,
LLM gateway, budget, fetcher, HTTP client) are injected by agents/graph.py via
functools.partial, not carried in state (state is data, per ADR 0001)."""

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.agents import prompts
from fieldbrief.agents.llm_schemas import ClaimVerification, ExtractionResult, SynthesisResult
from fieldbrief.agents.state import GraphState
from fieldbrief.db.models import Claim, Source
from fieldbrief.db.repositories import (
    add_claim,
    get_fresh_claims,
    get_or_create_source,
    save_report,
)
from fieldbrief.fetch.clean import clean_html, content_hash
from fieldbrief.fetch.http import Fetcher
from fieldbrief.fetch.search import search
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway, Task
from fieldbrief.reports.confidence import compute_citation

QUICK_TIER_FIELDS = [
    "profile.description",
    "profile.hq_founded",
    "profile.employees",
    "offering.products",
    "customers.segments",
    "revenue.model",
    "financials.revenue",
    "events.recent",
]

MAX_RETRY_ROUNDS = {"quick": 1, "standard": 2, "deep": 3}
MAX_ATTEMPTS_PER_FIELD = 2

FRESHNESS_MAX_AGE: dict[str, timedelta] = {
    "events.recent": timedelta(days=7),
    "financials.revenue": timedelta(days=365),
}
DEFAULT_FRESHNESS_MAX_AGE = timedelta(days=30)


def _max_age(field: str) -> timedelta:
    return FRESHNESS_MAX_AGE.get(field, DEFAULT_FRESHNESS_MAX_AGE)


def _source_type(url: str, company_domain: str | None) -> str:
    if company_domain and urlparse(url).netloc == urlparse(company_domain).netloc:
        return "official"
    return "news"


async def plan_node(state: GraphState, *, session: AsyncSession) -> dict[str, Any]:
    company_id = uuid.UUID(state["company_id"])
    gaps = []
    for field in QUICK_TIER_FIELDS:
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
    """One research round: fetch 1-2 pages likely to cover the current gaps,
    extract+verify claims from each. design.md §9: "fetch -> extract claims for
    all known companies on the page" — one page naturally yields claims for
    several fields, so this isn't a per-field fetch loop."""
    if not state["gaps"]:
        return {}

    company_id = uuid.UUID(state["company_id"])
    company_name = state["company_name"]
    company_domain = state["company_domain"]
    gap_fields = [g["field"] for g in state["gaps"]]
    claim_ids = list(state["claim_ids"])

    urls: list[str] = []
    if company_domain:
        urls.append(company_domain)
    else:
        results = await search(
            http_client, searxng_url, f"{company_name} company overview", limit=3
        )
        if results:
            urls.append(results[0].url)

    if "events.recent" in gap_fields:
        news_results = await search(http_client, searxng_url, f"{company_name} news", limit=3)
        if news_results:
            urls.append(news_results[0].url)

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
            prompts.extraction_prompt(company_name, cleaned, gap_fields),
            ExtractionResult,
            budget,
            report_id=state["report_id"],
            stage="research",
        )
        for extracted in extraction.claims:
            if extracted.field not in QUICK_TIER_FIELDS:
                continue  # model must stick to the requested taxonomy
            snippet = cleaned[:500]
            claim = await add_claim(
                session,
                company_id=company_id,
                source_id=source.id,
                field=extracted.field,
                statement=extracted.statement,
                value=extracted.value,
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


def should_continue_researching(state: GraphState, *, tier: str) -> str:
    max_rounds = MAX_RETRY_ROUNDS[tier]
    if state["gaps"] and state["retry_round"] < max_rounds:
        return "loop"
    return "advance"


async def analyze_node(state: GraphState, *, session: AsyncSession) -> dict[str, Any]:
    """Deterministic CompanySnapshot assembly — grouping already-extracted,
    already-tagged claims by field is data wrangling, not judgment, so no LLM
    call here (cheaper, and nothing to hallucinate). VRIO/BMC (target-only,
    Standard tier+) will live here once stage 4 adds those fields — this node
    is the designed seam for them, not dead weight."""
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
        "is_target": True,
        "description": citation_for("profile.description"),
        "products": citation_for("offering.products"),
        "customer_segments": citation_for("customers.segments"),
        "revenue_model": citation_for("revenue.model"),
        "revenue": citation_for("financials.revenue"),
        "recent_events": citation_for("events.recent"),
        "freshness": {"as_of": date.today().isoformat(), "stale": False},
    }
    return {"company_snapshot": snapshot}


async def synthesize_node(
    state: GraphState, *, session: AsyncSession, gateway: LLMGateway, budget: Budget
) -> dict[str, Any]:
    company_id = uuid.UUID(state["company_id"])
    claims_result = await session.execute(
        select(Claim)
        .where(Claim.company_id == company_id, Claim.verified.is_(True))
        .order_by(Claim.extracted_at)
    )
    claims = list(claims_result.scalars().all())

    opportunities: list[dict[str, Any]] = []
    threats: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []

    if claims:
        synthesis = await gateway.call(
            Task.SYNTHESIZE,
            prompts.synthesis_prompt(state["company_name"], claims),
            SynthesisResult,
            budget,
            report_id=state["report_id"],
            stage="synthesize",
        )

        def resolve_ids(indices: list[int]) -> list[str]:
            return [str(claims[i].id) for i in indices if 0 <= i < len(claims)]

        for item in synthesis.opportunities:
            claim_ids = resolve_ids(item.source_claim_indices)
            if not claim_ids:
                continue  # an uncited finding isn't a finding — drop it, don't invent a citation
            opportunities.append(
                {
                    "statement": item.statement,
                    "derived_from": item.derived_from,
                    "claim_ids": claim_ids,
                }
            )
        for item in synthesis.threats:
            claim_ids = resolve_ids(item.source_claim_indices)
            if not claim_ids:
                continue
            threats.append(
                {
                    "statement": item.statement,
                    "derived_from": item.derived_from,
                    "claim_ids": claim_ids,
                }
            )
        swot_count = len(opportunities) + len(threats)
        for rec in synthesis.recommendations:
            valid_swot_refs = [i for i in rec.swot_indices if 0 <= i < swot_count]
            if not valid_swot_refs:
                continue  # ADR 0009: a recommendation with no real swot_refs is a synthesis bug
            recommendations.append(
                {
                    "statement": rec.statement,
                    "rationale": rec.rationale,
                    "priority": rec.priority,
                    "swot_refs": valid_swot_refs,
                    "claim_ids": resolve_ids(rec.source_claim_indices),
                }
            )

    gaps = [
        {
            "company_id": state["company_id"],
            "field": gap["field"],
            "attempts": gap["attempts"],
            "reason": "not_found" if gap["attempts"] >= MAX_ATTEMPTS_PER_FIELD else "budget_cutoff",
        }
        for gap in state["gaps"]
    ]

    report_json = {
        "meta": {
            "target_company_id": state["company_id"],
            "competitor_ids": [],
            "tier": state["tier"],
            "generated_at": datetime.now(UTC).isoformat(),
            "data_as_of": date.today().isoformat(),
        },
        "companies": [state["company_snapshot"]],
        "five_forces": {"forces": [], "freshness": None},
        "bmc": {"blocks": [], "freshness": None},
        "vrio": [],
        "positioning": {"x_axis": None, "y_axis": None, "companies": []},
        "swot": {
            "strengths": [],
            "weaknesses": [],
            "opportunities": opportunities,
            "threats": threats,
        },
        "recommendations": recommendations,
        "gaps": gaps,
    }
    return {"report_json": report_json}


async def deliver_node(
    state: GraphState, *, session: AsyncSession, budget: Budget
) -> dict[str, Any]:
    """Persists report_json + report_claims and commits — the one place in the
    graph that makes a run's results permanent (every other node only flushes,
    per the DB-layer tests' rollback-at-teardown pattern)."""
    report_json = state["report_json"] or {}
    gaps = report_json.get("gaps", [])
    has_budget_cutoff = any(g["reason"] == "budget_cutoff" for g in gaps)
    status = "partial" if gaps and has_budget_cutoff else "done"

    await save_report(
        session,
        report_id=uuid.UUID(state["report_id"]),
        report_json=report_json,
        status=status,
        cost_usd=budget.spent_usd,
        claim_ids=[uuid.UUID(cid) for cid in state["claim_ids"]],
    )
    await session.commit()
    return {}
