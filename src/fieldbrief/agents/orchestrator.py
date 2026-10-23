import asyncio
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.agents.competitors import discover_competitors
from fieldbrief.agents.field_checklist import Tier
from fieldbrief.agents.graph import build_company_research_graph
from fieldbrief.agents.nodes import analyze_node, plan_node
from fieldbrief.agents.progress import NullProgressSink, ProgressSink, Stage
from fieldbrief.agents.state import GraphState
from fieldbrief.agents.synthesis import CompanyRef, run_synthesis
from fieldbrief.consts import WIKIDATA_CANDIDATE_LIMIT
from fieldbrief.db.models import Claim, Company, Source
from fieldbrief.db.repositories import (
    add_report_competitors,
    get_or_create_company,
    save_report,
    upsert_competitor_relationship,
)
from fieldbrief.db.session import SessionFactory
from fieldbrief.exceptions import BudgetExceededError
from fieldbrief.fetch.http import Fetcher
from fieldbrief.fetch.wikidata import get_company_details, pick_company_like, search_candidates
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway
from fieldbrief.reports.render import collect_claim_ids
from fieldbrief.reports.schema import DEFAULT_AUDIENCE, Audience

_NULL_PROGRESS = NullProgressSink()


async def _source_type_counts(session: AsyncSession, claim_ids: list[str]) -> dict[str, int]:
    if not claim_ids:
        return {}
    rows = await session.execute(
        select(Source.source_type, Source.id)
        .join(Claim, Claim.source_id == Source.id)
        .where(Claim.id.in_([uuid.UUID(cid) for cid in claim_ids]))
    )
    seen_source_ids: set[uuid.UUID] = set()
    counts: dict[str, int] = {}
    for source_type, source_id in rows.all():
        if source_id in seen_source_ids:
            continue
        seen_source_ids.add(source_id)
        counts[source_type] = counts.get(source_type, 0) + 1
    return counts


def _research_summary(
    *,
    source_type_counts: dict[str, int],
    target_gaps: list[dict[str, Any]],
    competitor_coverage: str,
) -> dict[str, Any]:
    """A deterministic, no-LLM-call account of what research actually
    happened, not what the system is capable of in principle — source
    mix, and a plain-English limitations list built from real gap reasons
    and the real competitor-coverage outcome."""
    reason_counts: dict[str, int] = {}
    for gap in target_gaps:
        reason_counts[gap["reason"]] = reason_counts.get(gap["reason"], 0) + 1

    limitations: list[str] = []
    if reason_counts.get("not_found"):
        limitations.append(f"{reason_counts['not_found']} field(s) could not be found")
    if reason_counts.get("partial"):
        limitations.append(f"{reason_counts['partial']} field(s) have only partial evidence")
    if reason_counts.get("budget_cutoff"):
        limitations.append("Research stopped early due to this report's cost/time budget")
    if competitor_coverage == "none":
        limitations.append("No competitors could be confirmed")
    elif competitor_coverage == "partial":
        limitations.append("Fewer competitors were confirmed than this tier normally covers")

    return {"source_type_counts": source_type_counts, "limitations": limitations}


def _competitor_coverage(competitor_count: int, competitors: list[Company]) -> str:
    if competitor_count == 0:
        return "not_requested"
    if not competitors:
        return "none"
    if len(competitors) < competitor_count:
        return "partial"
    return "full"


async def _resolve_competitor(
    name: str, *, wikidata_client: httpx.AsyncClient, session: AsyncSession
) -> Company | None:
    candidates = await search_candidates(wikidata_client, name, limit=WIKIDATA_CANDIDATE_LIMIT)
    if not candidates:
        return None
    best = await pick_company_like(wikidata_client, candidates)
    if best is None:
        return await get_or_create_company(session, name=name)
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
    progress: ProgressSink,
    edgar_client: httpx.AsyncClient | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    async with SessionFactory() as session:
        graph = build_company_research_graph(
            session=session,
            gateway=gateway,
            budget=budget,
            fetcher=fetcher,
            http_client=http_client,
            searxng_url=searxng_url,
            tier=tier,
            edgar_client=edgar_client,
            progress=progress,
            deadline=deadline,
        )
        initial: GraphState = {
            "report_id": str(report_id),
            "company_id": str(company.id),
            "company_name": company.name,
            "company_domain": company.domain,
            "company_cik": company.cik,
            "is_target": is_target,
            "tier": tier,
            "retry_round": 0,
            "gaps": [],
            "exhausted_gaps": [],
            "claim_ids": [],
            "company_snapshot": None,
        }
        try:
            result = await graph.ainvoke(initial)
        except BudgetExceededError:
            await progress.emit(
                Stage.RESEARCHING, f"{company.name}: budget exhausted, using research so far"
            )
            plan_result = await plan_node(initial, session=session)
            analyze_result = await analyze_node(initial, session=session)
            claims_result = await session.execute(
                select(Claim.id).where(Claim.company_id == company.id)
            )
            result = {
                "company_id": str(company.id),
                "gaps": plan_result["gaps"],
                "exhausted_gaps": [],
                "claim_ids": [str(cid) for (cid,) in claims_result.all()],
                "company_snapshot": analyze_result["company_snapshot"],
                "hit_budget_cutoff": True,
            }
        else:
            if deadline is not None and time.monotonic() >= deadline and result["gaps"]:
                result = {**result, "hit_budget_cutoff": True}
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
    progress: ProgressSink = _NULL_PROGRESS,
    edgar_client: httpx.AsyncClient | None = None,
    time_budget_seconds: float | None = None,
    audience: Audience = DEFAULT_AUDIENCE,
) -> dict[str, Any]:
    deadline = time.monotonic() + time_budget_seconds if time_budget_seconds is not None else None

    competitors: list[Company] = []
    if competitor_count > 0:
        await progress.emit(Stage.DISCOVERING, f"Finding up to {competitor_count} competitors")
        try:
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
        except BudgetExceededError:
            candidates = []
            await progress.emit(
                Stage.DISCOVERING, "Budget exhausted before competitor discovery — target only"
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

    competitor_coverage = _competitor_coverage(competitor_count, competitors)

    companies = [target_company, *competitors]
    await progress.emit(Stage.RESEARCHING, f"Researching {len(companies)} companies")
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
                progress=progress,
                edgar_client=edgar_client,
                deadline=deadline,
            )
            for company in companies
        )
    )

    await progress.emit(Stage.ANALYZING, "Building company profiles from research")

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
            "reason": gap.get("reason", "not_found"),
        }
        for result in results
        for gap in result.get("exhausted_gaps", [])
    ] + [
        {
            "company_id": result["company_id"],
            "field": gap["field"],
            "attempts": gap["attempts"],
            "reason": "budget_cutoff" if result.get("hit_budget_cutoff") else "not_found",
        }
        for result in results
        for gap in result["gaps"]
    ]

    await progress.emit(Stage.SYNTHESIZING, "Running frameworks and SWOT")
    report_json = await run_synthesis(
        session=session,
        gateway=gateway,
        budget=budget,
        report_id=report_id,
        tier=tier,
        companies=company_refs,
        gaps=all_gaps,
        audience=audience,
    )

    report_json["meta"]["competitor_coverage"] = competitor_coverage
    # Every claim this report actually cites, not just claims extracted in
    # this run's graph execution — a field satisfied by an already-fresh
    # claim from an earlier run never re-enters research_node, so
    # all_claim_ids alone would silently miss most of a cache-heavy report's
    # real sources.
    cited_claim_ids = list(collect_claim_ids(report_json))
    report_json["meta"]["research_summary"] = _research_summary(
        source_type_counts=await _source_type_counts(session, cited_claim_ids),
        target_gaps=[g for g in all_gaps if g["company_id"] == str(target_company.id)],
        competitor_coverage=competitor_coverage,
    )

    has_budget_cutoff = any(g["reason"] == "budget_cutoff" for g in all_gaps)
    synthesis_budget_cutoff = report_json["meta"]["synthesis_budget_cutoff"]
    status = "partial" if (all_gaps and has_budget_cutoff) or synthesis_budget_cutoff else "done"

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

    await progress.emit(Stage.DONE, f"Report {status}")
    return report_json
