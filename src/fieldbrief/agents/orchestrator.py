"""Runs one full report: discover competitors, research every company in
parallel (ADR 0010's outer fan-out — see agents/graph.py's module docstring
for why that's plain asyncio.gather, not a second LangGraph layer), then the
cross-company synthesis join. The one function the CLI (and, later, the
worker) calls."""

import asyncio
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.agents.competitors import discover_competitors
from fieldbrief.agents.field_checklist import Tier
from fieldbrief.agents.graph import build_company_research_graph
from fieldbrief.agents.nodes import MAX_ATTEMPTS_PER_FIELD
from fieldbrief.agents.state import GraphState
from fieldbrief.agents.synthesis import CompanyRef, run_synthesis
from fieldbrief.db.models import Company
from fieldbrief.db.repositories import (
    add_report_competitors,
    get_or_create_company,
    save_report,
    upsert_competitor_relationship,
)
from fieldbrief.db.session import SessionFactory
from fieldbrief.fetch.http import Fetcher
from fieldbrief.fetch.wikidata import get_company_details, pick_company_like, search_candidates
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway


async def _resolve_competitor(
    name: str, *, wikidata_client: httpx.AsyncClient, session: AsyncSession
) -> Company | None:
    candidates = await search_candidates(wikidata_client, name, limit=5)
    if not candidates:
        return None
    best = await pick_company_like(wikidata_client, candidates) or candidates[0]
    details = await get_company_details(wikidata_client, best.qid)
    return await get_or_create_company(
        session,
        name=details.name,
        domain=details.domain,
        aliases=details.aliases,
        ticker=details.ticker,
        cik=details.cik,
        industry=details.industry,
    )


async def _research_company(
    *,
    company: Company,
    is_target: bool,
    report_id: uuid.UUID,
    tier: Tier,
    gateway: LLMGateway,
    budget: Budget,
    fetcher: Fetcher,
    http_client: httpx.AsyncClient,
    searxng_url: str,
) -> dict[str, Any]:
    """Its own session, not the caller's — this runs concurrently with every
    other company's research (asyncio.gather below), and AsyncSession isn't
    safe for concurrent use. Commits at the end: a company's claims are
    shared, durable research (ADR 0004) independent of whether the report
    they were gathered for succeeds."""
    async with SessionFactory() as session:
        graph = build_company_research_graph(
            session=session,
            gateway=gateway,
            budget=budget,
            fetcher=fetcher,
            http_client=http_client,
            searxng_url=searxng_url,
            tier=tier,
        )
        initial: GraphState = {
            "report_id": str(report_id),
            "company_id": str(company.id),
            "company_name": company.name,
            "company_domain": company.domain,
            "is_target": is_target,
            "tier": tier,
            "retry_round": 0,
            "gaps": [],
            "claim_ids": [],
            "company_snapshot": None,
        }
        result = await graph.ainvoke(initial)
        await session.commit()
        return result


async def run_report(
    *,
    session: AsyncSession,
    gateway: LLMGateway,
    budget: Budget,
    fetcher: Fetcher,
    http_client: httpx.AsyncClient,
    wikidata_client: httpx.AsyncClient,
    searxng_url: str,
    report_id: uuid.UUID,
    target_company: Company,
    tier: Tier,
    competitor_count: int,
) -> dict[str, Any]:
    competitors: list[Company] = []
    if competitor_count > 0:
        candidates = await discover_competitors(
            target_company.name,
            http_client=http_client,
            fetcher=fetcher,
            gateway=gateway,
            budget=budget,
            searxng_url=searxng_url,
            report_id=str(report_id),
            limit=competitor_count,
        )
        for candidate in candidates:
            resolved = await _resolve_competitor(
                candidate.name, wikidata_client=wikidata_client, session=session
            )
            if resolved is None or resolved.id == target_company.id:
                continue
            await upsert_competitor_relationship(
                session, target_company.id, resolved.id, candidate.relevance
            )
            competitors.append(resolved)
        await session.commit()

    companies = [target_company, *competitors]
    results = await asyncio.gather(
        *(
            _research_company(
                company=company,
                is_target=(company.id == target_company.id),
                report_id=report_id,
                tier=tier,
                gateway=gateway,
                budget=budget,
                fetcher=fetcher,
                http_client=http_client,
                searxng_url=searxng_url,
            )
            for company in companies
        )
    )

    company_refs = []
    for company, result in zip(companies, results, strict=True):
        snapshot = result["company_snapshot"]
        assert snapshot is not None, f"analyze_node must always set company_snapshot ({company.id})"
        company_refs.append(
            CompanyRef(
                company_id=company.id,
                name=company.name,
                is_target=(company.id == target_company.id),
                snapshot=snapshot,
            )
        )
    all_claim_ids = [cid for result in results for cid in result["claim_ids"]]
    all_gaps = [
        {
            "company_id": result["company_id"],
            "field": gap["field"],
            "attempts": gap["attempts"],
            "reason": (
                "not_found" if gap["attempts"] >= MAX_ATTEMPTS_PER_FIELD else "budget_cutoff"
            ),
        }
        for result in results
        for gap in result["gaps"]
    ]

    report_json = await run_synthesis(
        session=session,
        gateway=gateway,
        budget=budget,
        report_id=report_id,
        tier=tier,
        companies=company_refs,
        gaps=all_gaps,
    )

    has_budget_cutoff = any(g["reason"] == "budget_cutoff" for g in all_gaps)
    status = "partial" if all_gaps and has_budget_cutoff else "done"

    await save_report(
        session,
        report_id=report_id,
        report_json=report_json,
        status=status,
        cost_usd=budget.spent_usd,
        claim_ids=[uuid.UUID(cid) for cid in all_claim_ids],
    )
    if competitors:
        await add_report_competitors(session, report_id, [c.id for c in competitors])
    await session.commit()

    return report_json
