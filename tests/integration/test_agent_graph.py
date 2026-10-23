"""Full orchestrator run (target + 1 competitor, standard tier) against real
Postgres, with network and LLM calls mocked. Covers competitor discovery,
Five Forces/VRIO/BMC/positioning, and the deterministic SWOT roll-up — the
actual payoff of stage 4, not just stage 3's single-company slice.

Not `live` (no external APIs hit) — but this does commit for real (per-company
research sessions + the final save_report), so it cleans up its own rows
explicitly instead of relying on the rollback-at-teardown pattern.
"""

import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
import respx
from sqlalchemy import delete, select

from fieldbrief.agents.competitors import CompetitorCandidate, CompetitorDiscoveryResult
from fieldbrief.agents.llm_schemas import (
    ClaimVerification,
    ExtractionResult,
    FrameworkSynthesisResult,
)
from fieldbrief.agents.nodes import (
    analyze_node,
    gap_check_node,
    plan_node,
    research_node,
    should_continue_researching,
)
from fieldbrief.agents.orchestrator import _research_company, run_report
from fieldbrief.agents.progress import NullProgressSink
from fieldbrief.db.models import (
    Claim,
    Company,
    CompanyCompetitor,
    Report,
    ReportClaim,
    ReportCompetitor,
    Source,
)
from fieldbrief.db.repositories import (
    add_claim,
    create_report,
    get_or_create_cli_user,
    get_or_create_company,
    get_or_create_source,
)
from fieldbrief.db.session import SessionFactory, engine
from fieldbrief.fetch.edgar import SUBMISSIONS_URL
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
                        "field": "financials.funding",
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
                    {
                        "company_index": target_index,
                        "x": 0.6,
                        "y": 0.7,
                        "source_claim_indices": [resources_idx],
                    },
                    {
                        "company_index": competitor_index,
                        "x": 0.5,
                        "y": 0.3,
                        "source_claim_indices": [forces_idx],
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

    # fake_search's URLs are a fixed constant (https://search.example.com/{topic}),
    # shared across every *invocation* of this test, not just within one run.
    # Since the dedup fix (v0.5.0) legitimately lets a new company reuse an
    # already-seen Source, a prior failed run's leftover claims on that
    # Source would make *this* run's cleanup hit a cross-run FK violation —
    # not a bug in the fix, a pre-existing test fragility it exposed. A
    # per-invocation tag keeps each run's Sources genuinely its own.
    run_tag = uuid4().hex[:8]

    async def fake_search_this_run(
        client: object, searxng_url: str, query: str, limit: int = 10
    ) -> list[SearchResult]:
        topic = _topic_for_query(query)
        return [
            SearchResult(
                title=topic, url=f"https://search-{run_tag}.example.com/{topic}", snippet=None
            )
        ]

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
                patch("fieldbrief.agents.nodes.search", new=fake_search_this_run),
                patch("fieldbrief.agents.competitors.search", new=fake_search_this_run),
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
            # Claims for every company deleted before any Source row: two
            # companies can now legitimately share a Source (dedup is scoped
            # per company/source/field, not a blanket skip for a second
            # company), so deleting sources company-by-company could hit one
            # still referenced by a company not yet cleaned up.
            all_source_ids: set[uuid.UUID] = set()
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
                all_source_ids.update(row[0] for row in claims_result.all())
                await cleanup_session.execute(delete(Claim).where(Claim.company_id == company_id))
            if all_source_ids:
                await cleanup_session.execute(delete(Source).where(Source.id.in_(all_source_ids)))
            for company_id in created_company_ids:
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


async def _cleanup_company(company_id: uuid.UUID, report_id: uuid.UUID | None) -> None:
    async with SessionFactory() as cleanup_session:
        claims_result = await cleanup_session.execute(
            select(Claim.source_id).where(Claim.company_id == company_id)
        )
        source_ids = {row[0] for row in claims_result.all()}
        await cleanup_session.execute(delete(Claim).where(Claim.company_id == company_id))
        if source_ids:
            await cleanup_session.execute(delete(Source).where(Source.id.in_(source_ids)))
        if report_id is not None:
            await cleanup_session.execute(delete(Report).where(Report.id == report_id))
        await cleanup_session.execute(delete(Company).where(Company.id == company_id))
        await cleanup_session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_hq_founded_and_employees_reach_the_snapshot() -> None:
    """profile.hq_founded and profile.employees were extracted and verified
    like any other claim, but analyze_node never read them into the
    snapshot (no citation_for() call for either field) — report.html's
    {% if c.hq %} / {% if c.employees %} markup was always rendering against
    None. A real dropped-field bug, not a design decision."""

    async def fake_fetch_overview(self: Fetcher, url: str) -> str:
        return _page("HQ_TEST_OVERVIEW_PAGE")

    def route(*args: object, **kwargs: object) -> SimpleNamespace:
        schema = kwargs["response_format"]
        if schema is ExtractionResult:
            result = ExtractionResult(
                claims=[
                    {
                        "field": "profile.hq_founded",
                        "statement": "Headquartered in New York, founded 2005.",
                        "value": {"hq": "New York, USA", "founded_year": 2005},
                        "relevance": 0.9,
                    },
                    {
                        "field": "profile.employees",
                        "statement": "About 200 employees.",
                        "relevance": 0.9,
                    },
                ]
            )
            return _fake_llm_response(result.model_dump_json())
        if schema is ClaimVerification:
            return _fake_llm_response(
                ClaimVerification(verified=True, reason="checked").model_dump_json()
            )
        raise AssertionError(f"unexpected schema {schema}")

    created_company_id: uuid.UUID | None = None
    created_report_id: uuid.UUID | None = None
    try:
        async with SessionFactory() as session:
            user = await get_or_create_cli_user(session)
            domain = f"https://hq-test-{uuid4().hex[:8]}.example.com/"
            company = await get_or_create_company(session, name="HQ Test Co", domain=domain)
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
                patch.object(Fetcher, "fetch", fake_fetch_overview),
                patch(
                    "fieldbrief.llm.gateway.litellm.acompletion", new=AsyncMock(side_effect=route)
                ),
                patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
            ):
                base_state = {
                    "report_id": str(report.id),
                    "company_id": str(company.id),
                    "company_name": company.name,
                    "company_domain": domain,
                    "company_cik": None,
                    "is_target": True,
                    "tier": "quick",
                    "claim_ids": [],
                    "company_snapshot": None,
                }
                research_result = await research_node(
                    {
                        **base_state,
                        "retry_round": 0,
                        "gaps": [
                            {"field": "profile.hq_founded", "attempts": 0},
                            {"field": "profile.employees", "attempts": 0},
                        ],
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

            assert len(research_result["claim_ids"]) == 2

            analyze_result = await analyze_node(
                {
                    **base_state,
                    "retry_round": 1,
                    "gaps": [],
                    "claim_ids": research_result["claim_ids"],
                },
                session=session,
            )
            snapshot = analyze_result["company_snapshot"]
            assert snapshot["hq"] == "New York, USA"
            assert snapshot["founded"] == "2005"
            assert snapshot["employees"] is not None
            await session.commit()
    finally:
        if created_company_id is not None:
            await _cleanup_company(created_company_id, created_report_id)


@pytest.mark.asyncio
@respx.mock
async def test_research_node_sources_financials_from_edgar_when_cik_present() -> None:
    """A company with a CIK gets financials claims from its real SEC filing
    (source_type="filing", reports/confidence.py's highest reliability tier)
    instead of only scraped news/blog text — fetch/edgar.py existed and was
    tested but was never actually called from research_node before this."""
    cik = "1576789"
    document_url = "https://www.sec.gov/Archives/edgar/data/1576789/000162828026015222/filing.htm"
    respx.get(SUBMISSIONS_URL.format(cik=int(cik))).mock(
        return_value=httpx.Response(
            200,
            json={
                "filings": {
                    "recent": {
                        "form": ["20-F"],
                        "filingDate": ["2026-03-05"],
                        "accessionNumber": ["0001628280-26-015222"],
                        "primaryDocument": ["filing.htm"],
                    }
                }
            },
        )
    )
    respx.get(document_url).mock(
        return_value=httpx.Response(200, text=_page("EDGAR_FILING_MARKER"))
    )

    async def fake_fetch_empty(self: Fetcher, url: str) -> None:
        return None  # the scraped-page path contributes nothing in this test

    def route(*args: object, **kwargs: object) -> SimpleNamespace:
        schema = kwargs["response_format"]
        if schema is ExtractionResult:
            result = ExtractionResult(
                claims=[
                    {
                        "field": "financials.revenue",
                        "statement": "Reported $500M revenue in the filing.",
                        "relevance": 0.9,
                    }
                ]
            )
            return _fake_llm_response(result.model_dump_json())
        if schema is ClaimVerification:
            return _fake_llm_response(
                ClaimVerification(verified=True, reason="checked").model_dump_json()
            )
        raise AssertionError(f"unexpected schema {schema}")

    created_company_id: uuid.UUID | None = None
    created_report_id: uuid.UUID | None = None
    try:
        async with SessionFactory() as session:
            user = await get_or_create_cli_user(session)
            domain = f"https://edgar-test-{uuid4().hex[:8]}.example.com/"
            company = await get_or_create_company(
                session, name="Edgar Test Co", domain=domain, cik=cik
            )
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
            edgar_client = httpx.AsyncClient()

            with (
                patch.object(Fetcher, "fetch", fake_fetch_empty),
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
                        "company_cik": cik,
                        "is_target": True,
                        "tier": "quick",
                        "retry_round": 0,
                        "gaps": [{"field": "financials.revenue", "attempts": 0}],
                        "claim_ids": [],
                        "company_snapshot": None,
                    },
                    session=session,
                    gateway=gateway,
                    budget=budget,
                    fetcher=fetcher,
                    http_client=http_client,
                    searxng_url="http://unused.invalid",
                    edgar_client=edgar_client,
                )
            await http_client.aclose()
            await edgar_client.aclose()
            await fetcher.aclose()

            assert len(result["claim_ids"]) == 1
            claim = await session.get(Claim, uuid.UUID(result["claim_ids"][0]))
            assert claim is not None
            source = await session.get(Source, claim.source_id)
            assert source is not None
            assert source.source_type == "filing"
            assert source.url == document_url
            await session.commit()
    finally:
        if created_company_id is not None:
            await _cleanup_company(created_company_id, created_report_id)


@pytest.mark.asyncio
async def test_research_node_searches_resources_and_market_forces_independently() -> None:
    """resources.advantages and market.forces used to share one search query
    and one fetched page — a page about industry dynamics but no named
    resource left VRIO (and therefore SWOT's Strengths/Weaknesses, ADR 0009)
    empty on real runs. They must now trigger two independent searches."""
    queries_seen: list[str] = []

    async def recording_search(
        client: object, searxng_url: str, query: str, limit: int = 10
    ) -> list[SearchResult]:
        queries_seen.append(query)
        return []  # no results needed — this test only checks the queries made

    created_company_id: uuid.UUID | None = None
    created_report_id: uuid.UUID | None = None
    try:
        async with SessionFactory() as session:
            user = await get_or_create_cli_user(session)
            domain = f"https://search-split-{uuid4().hex[:8]}.example.com/"
            company = await get_or_create_company(session, name="Split Co", domain=domain)
            created_company_id = company.id
            report = await create_report(
                session, user_id=user.id, target_company_id=company.id, tier="standard"
            )
            created_report_id = report.id
            await session.commit()

            budget = Budget(limit_usd=5.0)
            gateway = LLMGateway()
            fetcher = Fetcher()
            http_client = httpx.AsyncClient()

            async def fake_fetch_none(self: Fetcher, url: str) -> None:
                return None

            with (
                patch.object(Fetcher, "fetch", fake_fetch_none),
                patch("fieldbrief.agents.nodes.search", new=recording_search),
            ):
                await research_node(
                    {
                        "report_id": str(report.id),
                        "company_id": str(company.id),
                        "company_name": company.name,
                        "company_domain": domain,
                        "company_cik": None,
                        "is_target": True,
                        "tier": "standard",
                        "retry_round": 0,
                        "gaps": [
                            {"field": "resources.advantages", "attempts": 0},
                            {"field": "market.forces", "attempts": 0},
                        ],
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

            assert "Split Co competitive advantage moat" in queries_seen
            assert "Split Co industry competition analysis" in queries_seen
    finally:
        if created_company_id is not None:
            await _cleanup_company(created_company_id, created_report_id)


@pytest.mark.asyncio
async def test_research_node_fetches_two_sources_for_corroboration_fields() -> None:
    """financials/market.forces fields feed "predictions" directly, so a
    second independent source is fetched in the SAME round instead of
    relying on a retry round that only fires while the gap stays open — one
    successful source used to close the gap immediately, so the
    corroboration bonus in reports/confidence.py was effectively dead code."""
    run_id = uuid4().hex[:8]
    overview_url = f"https://corrob-overview-{run_id}.example.com/"
    fund1_url = f"https://corrob-fund-{run_id}.example.com/one"
    fund2_url = f"https://corrob-fund-{run_id}.example.com/two"

    async def fake_search_funding(
        client: object, searxng_url: str, query: str, limit: int = 10
    ) -> list[SearchResult]:
        return [
            SearchResult(title="one", url=fund1_url, snippet=None),
            SearchResult(title="two", url=fund2_url, snippet=None),
        ]

    async def fake_fetch_by_url(self: Fetcher, url: str) -> str | None:
        if url == overview_url:
            return _page("CORROB_OVERVIEW_PAGE")
        if url == fund1_url:
            return _page("CORROB_FUND_ONE")
        if url == fund2_url:
            return _page("CORROB_FUND_TWO")
        return None

    def route(*args: object, **kwargs: object) -> SimpleNamespace:
        schema = kwargs["response_format"]
        messages = kwargs["messages"]
        page_text = messages[-1]["content"]
        if schema is ExtractionResult:
            if "CORROB_FUND_ONE" in page_text:
                result = ExtractionResult(
                    claims=[
                        {
                            "field": "financials.funding",
                            "statement": "Raised $10M Series A.",
                            "relevance": 0.9,
                        }
                    ]
                )
            elif "CORROB_FUND_TWO" in page_text:
                result = ExtractionResult(
                    claims=[
                        {
                            "field": "financials.funding",
                            "statement": "Reported $12M total funding.",
                            "relevance": 0.9,
                        }
                    ]
                )
            else:
                result = ExtractionResult(claims=[])
            return _fake_llm_response(result.model_dump_json())
        if schema is ClaimVerification:
            return _fake_llm_response(
                ClaimVerification(verified=True, reason="checked").model_dump_json()
            )
        raise AssertionError(f"unexpected schema {schema}")

    created_company_id: uuid.UUID | None = None
    created_report_id: uuid.UUID | None = None
    try:
        async with SessionFactory() as session:
            user = await get_or_create_cli_user(session)
            company = await get_or_create_company(session, name="Corrob Co", domain=overview_url)
            created_company_id = company.id
            report = await create_report(
                session, user_id=user.id, target_company_id=company.id, tier="standard"
            )
            created_report_id = report.id
            await session.commit()

            budget = Budget(limit_usd=5.0)
            gateway = LLMGateway()
            fetcher = Fetcher()
            http_client = httpx.AsyncClient()

            with (
                patch.object(Fetcher, "fetch", fake_fetch_by_url),
                patch("fieldbrief.agents.nodes.search", new=fake_search_funding),
                patch(
                    "fieldbrief.llm.gateway.litellm.acompletion", new=AsyncMock(side_effect=route)
                ),
                patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
            ):
                base_state = {
                    "report_id": str(report.id),
                    "company_id": str(company.id),
                    "company_name": company.name,
                    "company_domain": overview_url,
                    "company_cik": None,
                    "is_target": True,
                    "tier": "standard",
                    "claim_ids": [],
                    "company_snapshot": None,
                }
                result = await research_node(
                    {
                        **base_state,
                        "retry_round": 0,
                        "gaps": [{"field": "financials.funding", "attempts": 0}],
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

            assert len(result["claim_ids"]) == 2

            analyze_result = await analyze_node(
                {**base_state, "retry_round": 1, "gaps": [], "claim_ids": result["claim_ids"]},
                session=session,
            )
            citation = analyze_result["company_snapshot"]["funding"]
            assert citation["confidence"]["corroboration"] == 2
            await session.commit()
    finally:
        if created_company_id is not None:
            await _cleanup_company(created_company_id, created_report_id)


@pytest.mark.asyncio
async def test_research_node_extracts_for_a_second_company_sharing_an_unchanged_source() -> None:
    """The real dedup bug: a Source's content_hash is global, so "unchanged"
    used to mean "skip" for whoever asked, not just the company that already
    had a claim from it. A second company hitting the exact same page must
    still get its own claim from it."""
    shared_url = f"https://shared-page-{uuid4().hex[:8]}.example.com/"

    async def fake_fetch_shared(self: Fetcher, url: str) -> str:
        return _page("SHARED_PAGE_MARKER")

    def route(*args: object, **kwargs: object) -> SimpleNamespace:
        schema = kwargs["response_format"]
        if schema is ExtractionResult:
            result = ExtractionResult(
                claims=[
                    {
                        "field": "profile.description",
                        "statement": "Shared page fact.",
                        "relevance": 0.9,
                    }
                ]
            )
            return _fake_llm_response(result.model_dump_json())
        if schema is ClaimVerification:
            return _fake_llm_response(
                ClaimVerification(verified=True, reason="checked").model_dump_json()
            )
        raise AssertionError(f"unexpected schema {schema}")

    created_company_ids: list[uuid.UUID] = []
    created_report_ids: list[uuid.UUID] = []
    try:
        async with SessionFactory() as session:
            user = await get_or_create_cli_user(session)
            company_a = await get_or_create_company(
                session, name="Shared Page Co A", domain=f"https://a-{uuid4().hex[:8]}.example.com/"
            )
            company_b = await get_or_create_company(
                session, name="Shared Page Co B", domain=f"https://b-{uuid4().hex[:8]}.example.com/"
            )
            created_company_ids.extend([company_a.id, company_b.id])
            report_a = await create_report(
                session, user_id=user.id, target_company_id=company_a.id, tier="quick"
            )
            report_b = await create_report(
                session, user_id=user.id, target_company_id=company_b.id, tier="quick"
            )
            created_report_ids.extend([report_a.id, report_b.id])
            await session.commit()

            budget = Budget(limit_usd=5.0)
            gateway = LLMGateway()
            fetcher = Fetcher()
            http_client = httpx.AsyncClient()

            with (
                patch.object(Fetcher, "fetch", fake_fetch_shared),
                patch(
                    "fieldbrief.llm.gateway.litellm.acompletion", new=AsyncMock(side_effect=route)
                ),
                patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
            ):
                base_gaps = [{"field": "profile.description", "attempts": 0}]
                result_a = await research_node(
                    {
                        "report_id": str(report_a.id),
                        "company_id": str(company_a.id),
                        "company_name": company_a.name,
                        "company_domain": shared_url,
                        "company_cik": None,
                        "is_target": True,
                        "tier": "quick",
                        "retry_round": 0,
                        "gaps": base_gaps,
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
                result_b = await research_node(
                    {
                        "report_id": str(report_b.id),
                        "company_id": str(company_b.id),
                        "company_name": company_b.name,
                        "company_domain": shared_url,
                        "company_cik": None,
                        "is_target": True,
                        "tier": "quick",
                        "retry_round": 0,
                        "gaps": base_gaps,
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

            assert len(result_a["claim_ids"]) == 1
            # The regression: company B must ALSO get a claim from the same,
            # already-seen, unchanged source — not be silently skipped.
            assert len(result_b["claim_ids"]) == 1

            claim_a = await session.get(Claim, uuid.UUID(result_a["claim_ids"][0]))
            claim_b = await session.get(Claim, uuid.UUID(result_b["claim_ids"][0]))
            assert claim_a is not None
            assert claim_b is not None
            assert claim_a.source_id == claim_b.source_id  # genuinely the same shared Source
            assert claim_a.company_id != claim_b.company_id
            await session.commit()
    finally:
        async with SessionFactory() as cleanup_session:
            all_source_ids: set[uuid.UUID] = set()
            for cid in created_company_ids:
                cr = await cleanup_session.execute(
                    select(Claim.source_id).where(Claim.company_id == cid)
                )
                all_source_ids.update(row[0] for row in cr.all())
                await cleanup_session.execute(delete(Claim).where(Claim.company_id == cid))
            if all_source_ids:
                await cleanup_session.execute(delete(Source).where(Source.id.in_(all_source_ids)))
            for rid in created_report_ids:
                await cleanup_session.execute(delete(Report).where(Report.id == rid))
            for cid in created_company_ids:
                await cleanup_session.execute(delete(Company).where(Company.id == cid))
            await cleanup_session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_excerpt_is_stored_only_when_genuinely_verbatim() -> None:
    """A claim's stored snippet must be an actual quote from the page, never
    a model-generated string presented as if it were one — a claimed
    excerpt that isn't actually in the page is dropped, not displayed as a
    fabricated source quotation."""
    real_sentence = "This exact sentence genuinely appears on the page."
    page_html = (
        f"<html><body><article><p>{real_sentence} Padding so trafilatura treats this as "
        "real article content, long enough to not get rejected as boilerplate noise.</p>"
        "</article></body></html>"
    )

    async def fake_fetch_page(self: Fetcher, url: str) -> str:
        return page_html

    def route(*args: object, **kwargs: object) -> SimpleNamespace:
        schema = kwargs["response_format"]
        if schema is ExtractionResult:
            result = ExtractionResult(
                claims=[
                    {
                        "field": "profile.description",
                        "statement": "Page confirms the fact.",
                        "relevance": 0.9,
                        "excerpt": real_sentence,
                    },
                    {
                        "field": "offering.products",
                        "statement": "Another claim with a fabricated excerpt.",
                        "relevance": 0.9,
                        "excerpt": "This exact sentence never appears anywhere on the page.",
                    },
                ]
            )
            return _fake_llm_response(result.model_dump_json())
        if schema is ClaimVerification:
            return _fake_llm_response(
                ClaimVerification(verified=True, reason="checked").model_dump_json()
            )
        raise AssertionError(f"unexpected schema {schema}")

    created_company_id: uuid.UUID | None = None
    created_report_id: uuid.UUID | None = None
    try:
        async with SessionFactory() as session:
            user = await get_or_create_cli_user(session)
            domain = f"https://excerpt-test-{uuid4().hex[:8]}.example.com/"
            company = await get_or_create_company(session, name="Excerpt Test Co", domain=domain)
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
                patch.object(Fetcher, "fetch", fake_fetch_page),
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
                        "company_cik": None,
                        "is_target": True,
                        "tier": "quick",
                        "retry_round": 0,
                        "gaps": [
                            {"field": "profile.description", "attempts": 0},
                            {"field": "offering.products", "attempts": 0},
                        ],
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

            assert len(result["claim_ids"]) == 2
            claims_by_field = {}
            for cid in result["claim_ids"]:
                claim = await session.get(Claim, uuid.UUID(cid))
                assert claim is not None
                claims_by_field[claim.field] = claim

            assert claims_by_field["profile.description"].snippet == real_sentence
            assert claims_by_field["offering.products"].snippet == ""
            await session.commit()
    finally:
        if created_company_id is not None:
            await _cleanup_company(created_company_id, created_report_id)


@pytest.mark.asyncio
async def test_plan_node_requires_several_forces_before_closing_market_forces() -> None:
    """One tagged claim used to be enough to call market.forces "done" —
    even though Five Forces (and therefore SWOT's Threats/Opportunities)
    built from a single force is barely more than a guess."""
    try:
        async with SessionFactory() as session:
            domain = f"https://forces-test-{uuid4().hex[:8]}.example.com/"
            company = await get_or_create_company(session, name="Forces Test Co", domain=domain)
            source, _ = await get_or_create_source(session, url=domain, content_hash="h1")

            async def add_force_claim(force: str) -> None:
                claim = await add_claim(
                    session,
                    company_id=company.id,
                    source_id=source.id,
                    field="market.forces",
                    statement=f"{force} claim",
                    value={"force": force},
                    snippet="",
                    relevance=0.9,
                )
                claim.verified = True
                await session.flush()

            plan_state = {"company_id": str(company.id), "tier": "standard", "is_target": True}

            await add_force_claim("rivalry")
            gaps = await plan_node(plan_state, session=session)
            assert any(g["field"] == "market.forces" for g in gaps["gaps"])

            await add_force_claim("substitutes")
            await add_force_claim("buyer_power")
            gaps_after = await plan_node(plan_state, session=session)
            assert not any(g["field"] == "market.forces" for g in gaps_after["gaps"])

            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_research_company_degrades_to_partial_snapshot_on_budget_exhaustion() -> None:
    """A real $ budget running out mid-research used to crash the whole
    report: BudgetExceededError was never caught, so it propagated to the
    worker's generic except-Exception and the report became "failed" —
    violating "missing data is partial, never failed." It must instead
    degrade to whatever research made it in before the cutoff."""

    async def fake_fetch_overview(self: Fetcher, url: str) -> str:
        return _page("OVERVIEW_PAGE_MARKER")

    async def fake_search_none(
        client: object, searxng_url: str, query: str, limit: int = 10
    ) -> list[SearchResult]:
        return []

    created_company_id: uuid.UUID | None = None
    created_report_id: uuid.UUID | None = None
    try:
        async with SessionFactory() as session:
            user = await get_or_create_cli_user(session)
            domain = f"https://budget-cutoff-{uuid4().hex[:8]}.example.com/"
            company = await get_or_create_company(session, name="Budget Cutoff Co", domain=domain)
            created_company_id = company.id
            report = await create_report(
                session, user_id=user.id, target_company_id=company.id, tier="quick"
            )
            created_report_id = report.id
            await session.commit()

            budget = Budget(limit_usd=0.0)  # already exhausted before the first call
            gateway = LLMGateway()
            fetcher = Fetcher()
            http_client = httpx.AsyncClient()

            with (
                patch.object(Fetcher, "fetch", fake_fetch_overview),
                patch("fieldbrief.agents.nodes.search", new=fake_search_none),
            ):
                result = await _research_company(
                    company=company,
                    is_target=True,
                    report_id=report.id,
                    tier="quick",
                    gateway=gateway,
                    budget=budget,
                    fetcher=fetcher,
                    http_client=http_client,
                    searxng_url="http://unused.invalid",
                    progress=NullProgressSink(),
                )
            await http_client.aclose()
            await fetcher.aclose()

            assert result["hit_budget_cutoff"] is True
            assert result["company_snapshot"] is not None
            # Nothing was ever actually researched — every quick-tier field
            # is still a real gap, not silently dropped.
            assert result["gaps"]
            await session.commit()
    finally:
        if created_company_id is not None:
            await _cleanup_company(created_company_id, created_report_id)


def test_should_continue_researching_stops_at_the_time_deadline() -> None:
    """report_time_budget_seconds used to be defined and never read — no
    wall-clock enforcement existed at all. A past deadline must stop the
    retry loop even with open gaps and rounds left in the tier's budget."""
    state = {
        "gaps": [{"field": "profile.description", "attempts": 0}],
        "retry_round": 0,
    }
    assert should_continue_researching(state, tier="deep", deadline=None) == "loop"
    assert should_continue_researching(state, tier="deep", deadline=time.monotonic() - 1) == (
        "advance"
    )


@pytest.mark.asyncio
async def test_gap_check_node_stops_retrying_a_field_past_its_attempt_cap() -> None:
    try:
        async with SessionFactory() as session:
            domain = f"https://attempt-cap-{uuid4().hex[:8]}.example.com/"
            company = await get_or_create_company(session, name="Attempt Cap Co", domain=domain)

            state = {
                "company_id": str(company.id),
                "gaps": [{"field": "profile.description", "attempts": 0}],
                "exhausted_gaps": [],
                "retry_round": 1,
            }
            result = await gap_check_node(state, session=session, tier="quick")

            assert result["gaps"] == []
            assert result["exhausted_gaps"] == [{"field": "profile.description", "attempts": 1}]
            await session.rollback()
    finally:
        await engine.dispose()
