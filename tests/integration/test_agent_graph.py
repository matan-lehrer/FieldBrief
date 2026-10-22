"""Full orchestrator run (target + 1 competitor, standard tier) against real
Postgres, with network and LLM calls mocked. Covers competitor discovery,
Five Forces/VRIO/BMC/positioning, and the deterministic SWOT roll-up — the
actual payoff of stage 4, not just stage 3's single-company slice.

Not `live` (no external APIs hit) — but this does commit for real (per-company
research sessions + the final save_report), so it cleans up its own rows
explicitly instead of relying on the rollback-at-teardown pattern.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from fieldbrief.agents.competitors import CompetitorCandidate, CompetitorDiscoveryResult
from fieldbrief.agents.llm_schemas import (
    ClaimVerification,
    ExtractionResult,
    FrameworkSynthesisResult,
)
from fieldbrief.agents.nodes import research_node
from fieldbrief.agents.orchestrator import run_report
from fieldbrief.db.models import (
    Claim,
    Company,
    CompanyCompetitor,
    Report,
    ReportClaim,
    ReportCompetitor,
    Source,
)
from fieldbrief.db.repositories import create_report, get_or_create_cli_user, get_or_create_company
from fieldbrief.db.session import SessionFactory, engine
from fieldbrief.fetch.http import Fetcher
from fieldbrief.fetch.search import SearchResult
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway

OVERVIEW_MARKER = "OVERVIEW_PAGE_MARKER"
PRICING_MARKER = "PRICING_PAGE_MARKER"
FUNDING_MARKER = "FUNDING_PAGE_MARKER"
COMPETITIVE_MARKER = "COMPETITIVE_PAGE_MARKER"
REGULATION_MARKER = "REGULATION_PAGE_MARKER"
NEWS_MARKER = "NEWS_PAGE_MARKER"
COMPETITORS_MARKER = "COMPETITORS_PAGE_MARKER"

_PAGE_BODY = (
    "{marker} — a long enough paragraph of filler content so that trafilatura's "
    "readability heuristics treat this as the main article body rather than "
    "boilerplate noise, since very short pages get rejected regardless."
)


def _page(marker: str) -> str:
    return f"<html><body><article><p>{_PAGE_BODY.format(marker=marker)}</p></article></body></html>"


def _topic_for_query(query: str) -> str:
    q = query.lower()
    if "pricing" in q:
        return "pricing"
    if "funding" in q or "financials" in q:
        return "funding"
    if "competitive advantage" in q:
        return "competitive"
    if "regulation" in q:
        return "regulation"
    if "news" in q:
        return "news"
    if "competitors" in q:
        return "competitors"
    return "overview"


_MARKER_BY_TOPIC = {
    "overview": OVERVIEW_MARKER,
    "pricing": PRICING_MARKER,
    "funding": FUNDING_MARKER,
    "competitive": COMPETITIVE_MARKER,
    "regulation": REGULATION_MARKER,
    "news": NEWS_MARKER,
    "competitors": COMPETITORS_MARKER,
}


async def fake_search(
    client: object, searxng_url: str, query: str, limit: int = 10
) -> list[SearchResult]:
    topic = _topic_for_query(query)
    return [SearchResult(title=topic, url=f"https://search.example.com/{topic}", snippet=None)]


async def fake_fetch(self: Fetcher, url: str) -> str:
    topic = url.rsplit("/", 1)[-1]
    return _page(_MARKER_BY_TOPIC.get(topic, OVERVIEW_MARKER))


def _fake_llm_response(parsed_json: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=parsed_json))],
        usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20),
    )


def _route_llm_call(*args: object, **kwargs: object) -> SimpleNamespace:
    schema = kwargs["response_format"]
    messages = kwargs["messages"]
    page_text = messages[-1]["content"]

    if schema is ExtractionResult:
        if OVERVIEW_MARKER in page_text:
            result = ExtractionResult(
                claims=[
                    {
                        "field": "profile.description",
                        "statement": "Makes widgets.",
                        "relevance": 0.9,
                    },
                    {"field": "profile.hq_founded", "statement": "Founded 2010.", "relevance": 0.9},
                    {"field": "profile.employees", "statement": "~50 employees.", "relevance": 0.9},
                    {"field": "offering.products", "statement": "Widgets.", "relevance": 0.9},
                    {
                        "field": "customers.segments",
                        "statement": "Small businesses.",
                        "relevance": 0.9,
                    },
                    {"field": "revenue.model", "statement": "Subscriptions.", "relevance": 0.9},
                    {"field": "financials.revenue", "statement": "$42M revenue.", "relevance": 0.9},
                    {
                        "field": "offering.value_prop",
                        "statement": "Only widget maker with same-day delivery.",
                        "relevance": 0.9,
                        "bmc_block": "value_propositions",
                    },
                ]
            )
        elif PRICING_MARKER in page_text:
            result = ExtractionResult(
                claims=[
                    {
                        "field": "pricing.model",
                        "statement": "Tiered subscription pricing.",
                        "relevance": 0.9,
                    }
                ]
            )
        elif FUNDING_MARKER in page_text:
            result = ExtractionResult(
                claims=[
                    {
                        "field": "financials.profitability",
                        "statement": "Profitable since 2022.",
                        "relevance": 0.9,
                    },
                    {
                        "field": "financials.funding_or_cap",
                        "statement": "Raised $10M Series A.",
                        "relevance": 0.9,
                    },
                ]
            )
        elif COMPETITIVE_MARKER in page_text:
            result = ExtractionResult(
                claims=[
                    {
                        "field": "resources.advantages",
                        "statement": "Patented same-day delivery network.",
                        "value": {"resource": "delivery network"},
                        "relevance": 0.9,
                    },
                    {
                        "field": "market.forces",
                        "statement": "Many substitute widget makers exist.",
                        "value": {"force": "substitutes"},
                        "relevance": 0.9,
                    },
                ]
            )
        elif REGULATION_MARKER in page_text:
            result = ExtractionResult(
                claims=[
                    {
                        "field": "environment.regulation",
                        "statement": "Subject to product safety rules.",
                        "relevance": 0.9,
                    }
                ]
            )
        elif NEWS_MARKER in page_text:
            result = ExtractionResult(
                claims=[
                    {
                        "field": "events.recent",
                        "statement": "Closed a new funding round this month.",
                        "relevance": 0.9,
                    }
                ]
            )
        else:
            result = ExtractionResult(claims=[])
        return _fake_llm_response(result.model_dump_json())

    if schema is ClaimVerification:
        return _fake_llm_response(
            ClaimVerification(verified=True, reason="Matches snippet.").model_dump_json()
        )

    if schema is CompetitorDiscoveryResult:
        result = CompetitorDiscoveryResult(
            competitors=[CompetitorCandidate(name="Gadget Co", relevance=0.9)]
        )
        return _fake_llm_response(result.model_dump_json())

    if schema is FrameworkSynthesisResult:
        company_block = messages[-1]["content"].split("Numbered claims:")[0]
        claims_block = messages[-1]["content"].split("Numbered claims:")[1]

        def find_index(marker: str) -> int:
            for line in claims_block.splitlines():
                if marker in line:
                    return int(line.split("]")[0].strip("["))
            raise AssertionError(f"no claim line contains {marker!r}")

        forces_idx = find_index("Many substitute widget makers")
        resources_idx = find_index("Patented same-day delivery network")
        news_idx = find_index("Closed a new funding round")
        regulation_idx = find_index("Subject to product safety rules")

        target_index = next(
            int(line.split("]")[0].strip("["))
            for line in company_block.splitlines()
            if "(target)" in line
        )
        competitor_index = 1 - target_index

        result = FrameworkSynthesisResult(
            five_forces=[
                {
                    "force": "substitutes",
                    "rating": "high",
                    "rationale": "Many substitute widget makers exist.",
                    "source_claim_indices": [forces_idx],
                }
            ],
            vrio=[
                {
                    "resource": "delivery network",
                    "valuable": True,
                    "rare": True,
                    "inimitable": True,
                    "organized": True,
                    "rationale": "Patented and hard to replicate.",
                    "source_claim_indices": [resources_idx],
                }
            ],
            positioning={
                "x_axis_rationale": "Both charge subscription pricing in a similar band.",
                "y_axis_rationale": "Target is broader; competitor is more niche.",
                "companies": [
                    {"company_index": target_index, "x": 0.6, "y": 0.7, "source_claim_indices": []},
                    {
                        "company_index": competitor_index,
                        "x": 0.5,
                        "y": 0.3,
                        "source_claim_indices": [],
                    },
                ],
            },
            other_opportunities=[],
            other_threats=[
                {
                    "statement": "New regulation could raise compliance costs.",
                    "derived_from": "regulation",
                    "source_claim_indices": [regulation_idx],
                }
            ],
            recommendations=[
                {
                    "statement": "Invest further in the delivery network moat.",
                    "rationale": "It's a sustained advantage worth reinforcing.",
                    "priority": "high",
                    "source_claim_indices": [resources_idx],
                },
                {
                    "statement": "Prepare a regulatory compliance plan.",
                    "rationale": "Follows from the news of upcoming scrutiny.",
                    "priority": "medium",
                    "source_claim_indices": [news_idx, regulation_idx],
                },
            ],
        )
        return _fake_llm_response(result.model_dump_json())

    raise AssertionError(f"unexpected schema {schema}")


async def _fake_resolve_competitor(
    name: str, *, wikidata_client: object, session: object
) -> Company:
    return await get_or_create_company(
        session, name=name, domain=f"https://gadget-{uuid4().hex[:8]}.example.com/"
    )


@pytest.mark.asyncio
async def test_full_report_with_competitor_covers_frameworks_and_swot_rollup() -> None:
    target_domain = f"https://acme-{uuid4().hex[:8]}.example.com/"
    created_company_ids: list[uuid.UUID] = []
    created_report_id = None

    try:
        async with SessionFactory() as session:
            user = await get_or_create_cli_user(session)
            target = await get_or_create_company(session, name="Acme Corp", domain=target_domain)
            created_company_ids.append(target.id)
            report = await create_report(
                session, user_id=user.id, target_company_id=target.id, tier="standard"
            )
            created_report_id = report.id
            await session.commit()

            budget = Budget(limit_usd=5.0)
            gateway = LLMGateway()
            fetcher = Fetcher()
            http_client = httpx.AsyncClient()
            wikidata_client = httpx.AsyncClient()

            with (
                patch.object(Fetcher, "fetch", fake_fetch),
                patch("fieldbrief.agents.nodes.search", new=fake_search),
                patch("fieldbrief.agents.competitors.search", new=fake_search),
                patch(
                    "fieldbrief.agents.orchestrator._resolve_competitor",
                    new=_fake_resolve_competitor,
                ),
                patch(
                    "fieldbrief.llm.gateway.litellm.acompletion",
                    new=AsyncMock(side_effect=_route_llm_call),
                ),
                patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
            ):
                report_json = await run_report(
                    session=session,
                    gateway=gateway,
                    budget=budget,
                    fetcher=fetcher,
                    http_client=http_client,
                    wikidata_client=wikidata_client,
                    searxng_url="http://unused.invalid",
                    report_id=report.id,
                    target_company=target,
                    tier="standard",
                    competitor_count=1,
                )
            await http_client.aclose()
            await wikidata_client.aclose()
            await fetcher.aclose()

        assert len(report_json["companies"]) == 2
        created_company_ids.extend(uuid.UUID(cid) for cid in report_json["meta"]["competitor_ids"])

        assert report_json["five_forces"]["forces"][0]["force"] == "substitutes"
        assert report_json["five_forces"]["forces"][0]["rating"] == "high"

        assert report_json["vrio"][0]["resource"] == "delivery network"
        assert report_json["vrio"][0]["competitive_implication"] == "sustained_advantage"

        assert any(b["block"] == "value_propositions" for b in report_json["bmc"]["blocks"])

        assert report_json["positioning"]["x_axis"]["name"] == "Price"
        assert len(report_json["positioning"]["companies"]) == 2

        # ADR 0009 roll-up: substitutes=high -> Threat; sustained_advantage -> Strength.
        assert any("substitute" in t["statement"].lower() for t in report_json["swot"]["threats"])
        assert any(s["derived_from"] == "vrio" for s in report_json["swot"]["strengths"])
        assert any(t["derived_from"] == "regulation" for t in report_json["swot"]["threats"])

        assert len(report_json["recommendations"]) == 2
        for rec in report_json["recommendations"]:
            assert rec["swot_refs"]  # every recommendation links to a real SWOT item

        async with SessionFactory() as verify_session:
            db_report = await verify_session.get(Report, report.id)
            assert db_report is not None
            assert db_report.status == "done"

            competitors_result = await verify_session.execute(
                select(CompanyCompetitor).where(CompanyCompetitor.company_id == target.id)
            )
            competitor_rows = list(competitors_result.scalars().all())
            assert len(competitor_rows) == 1

            report_competitors = await verify_session.execute(
                select(ReportCompetitor).where(ReportCompetitor.report_id == report.id)
            )
            assert len(list(report_competitors.scalars().all())) == 1
    finally:
        async with SessionFactory() as cleanup_session:
            if created_report_id is not None:
                await cleanup_session.execute(
                    delete(ReportClaim).where(ReportClaim.report_id == created_report_id)
                )
                await cleanup_session.execute(
                    delete(ReportCompetitor).where(ReportCompetitor.report_id == created_report_id)
                )
                await cleanup_session.execute(delete(Report).where(Report.id == created_report_id))
            for company_id in created_company_ids:
                await cleanup_session.execute(
                    delete(CompanyCompetitor).where(
                        (CompanyCompetitor.company_id == company_id)
                        | (CompanyCompetitor.competitor_id == company_id)
                    )
                )
                claims_result = await cleanup_session.execute(
                    select(Claim.source_id).where(Claim.company_id == company_id)
                )
                source_ids = {row[0] for row in claims_result.all()}
                await cleanup_session.execute(delete(Claim).where(Claim.company_id == company_id))
                if source_ids:
                    await cleanup_session.execute(delete(Source).where(Source.id.in_(source_ids)))
                await cleanup_session.execute(delete(Company).where(Company.id == company_id))
            await cleanup_session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_research_node_verifies_claims_from_anywhere_on_the_page() -> None:
    """Regression test for a real bug found reading the code after a live
    run came back with almost everything "not found": verification used to
    check every claim against snippet = cleaned[:500] — always the first
    500 characters of the page, regardless of where the claim actually came
    from. A claim correctly extracted from later in the page always failed
    this bogus check, so gap_check_node never cleared its gap. This fact
    sits well past char 500; the claim must still verify true."""
    marker = "ACME_LATE_PAGE_FACT"
    long_prefix = "Irrelevant filler sentence. " * 30
    assert len(long_prefix) > 500
    page_html = (
        f"<html><body><article><p>{long_prefix}{marker} is a real, specific fact about "
        "Acme Corp, long enough for trafilatura to treat as genuine article content.</p>"
        "</article></body></html>"
    )

    async def fake_fetch_long_page(self: Fetcher, url: str) -> str:
        return page_html

    captured_verify_prompts: list[str] = []

    def route(*args: object, **kwargs: object) -> SimpleNamespace:
        schema = kwargs["response_format"]
        messages = kwargs["messages"]
        if schema is ExtractionResult:
            result = ExtractionResult(
                claims=[{"field": "profile.description", "statement": marker, "relevance": 0.9}]
            )
            return _fake_llm_response(result.model_dump_json())
        if schema is ClaimVerification:
            prompt_text = messages[-1]["content"]
            captured_verify_prompts.append(prompt_text)
            verified = marker in prompt_text
            return _fake_llm_response(
                ClaimVerification(verified=verified, reason="checked").model_dump_json()
            )
        raise AssertionError(f"unexpected schema {schema}")

    created_company_id: uuid.UUID | None = None
    created_report_id: uuid.UUID | None = None
    try:
        async with SessionFactory() as session:
            user = await get_or_create_cli_user(session)
            domain = f"https://acme-late-{uuid4().hex[:8]}.example.com/"
            company = await get_or_create_company(session, name="Acme Corp", domain=domain)
            created_company_id = company.id
            report = await create_report(
                session, user_id=user.id, target_company_id=company.id, tier="quick"
            )
            created_report_id = report.id
            await session.commit()

            budget = Budget(limit_usd=5.0)
            gateway = LLMGateway()
            fetcher = Fetcher()
            http_client = httpx.AsyncClient()

            with (
                patch.object(Fetcher, "fetch", fake_fetch_long_page),
                patch(
                    "fieldbrief.llm.gateway.litellm.acompletion", new=AsyncMock(side_effect=route)
                ),
                patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
            ):
                result = await research_node(
                    {
                        "report_id": str(report.id),
                        "company_id": str(company.id),
                        "company_name": company.name,
                        "company_domain": domain,
                        "is_target": True,
                        "tier": "quick",
                        "retry_round": 0,
                        "gaps": [{"field": "profile.description", "attempts": 0}],
                        "claim_ids": [],
                        "company_snapshot": None,
                    },
                    session=session,
                    gateway=gateway,
                    budget=budget,
                    fetcher=fetcher,
                    http_client=http_client,
                    searxng_url="http://unused.invalid",
                )
            await http_client.aclose()
            await fetcher.aclose()

            assert len(captured_verify_prompts) == 1
            assert marker in captured_verify_prompts[0]

            assert len(result["claim_ids"]) == 1
            claim = await session.get(Claim, uuid.UUID(result["claim_ids"][0]))
            assert claim is not None
            assert claim.verified is True
            await session.commit()
    finally:
        async with SessionFactory() as cleanup_session:
            if created_company_id is not None:
                claims_result = await cleanup_session.execute(
                    select(Claim.source_id).where(Claim.company_id == created_company_id)
                )
                source_ids = {row[0] for row in claims_result.all()}
                await cleanup_session.execute(
                    delete(Claim).where(Claim.company_id == created_company_id)
                )
                if source_ids:
                    await cleanup_session.execute(delete(Source).where(Source.id.in_(source_ids)))
            if created_report_id is not None:
                await cleanup_session.execute(delete(Report).where(Report.id == created_report_id))
            if created_company_id is not None:
                await cleanup_session.execute(
                    delete(Company).where(Company.id == created_company_id)
                )
            await cleanup_session.commit()
        await engine.dispose()
