"""Full graph run against real Postgres, with network and LLM calls mocked.

Not `live` (no external APIs hit) — but `deliver_node` commits for real (that's
the behavior under test), so this cleans up its own rows explicitly instead of
relying on the rollback-at-teardown pattern the other DB tests use.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from fieldbrief.agents.graph import build_graph
from fieldbrief.agents.llm_schemas import ClaimVerification, ExtractionResult, SynthesisResult
from fieldbrief.db.models import Claim, Company, Report, ReportClaim, Source
from fieldbrief.db.repositories import create_report, get_or_create_cli_user, get_or_create_company
from fieldbrief.db.session import SessionFactory, engine
from fieldbrief.fetch.http import Fetcher
from fieldbrief.fetch.search import SearchResult
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway

OVERVIEW_HTML = """
<html><body><article>
<p>Acme Corp is a widget maker based in Springfield, founded in 2010. Acme
sells widgets to small businesses and makes money through subscriptions.
Acme reported revenue of $42 million last year, a long enough paragraph that
trafilatura treats it as real content rather than boilerplate noise here.
</p>
</article></body></html>
"""

NEWS_HTML = """
<html><body><article>
<p>Acme Corp announced this month that it closed a new funding round and
plans to expand into European markets next year, a long enough paragraph
that trafilatura treats it as real content rather than boilerplate noise.
</p>
</article></body></html>
"""


def _fake_llm_response(parsed: object) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=parsed))],  # set below
        usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20),
    )


def _route_llm_call(*args: object, **kwargs: object) -> SimpleNamespace:
    schema = kwargs["response_format"]
    if schema is ExtractionResult:
        messages = kwargs["messages"]
        page_text = messages[-1]["content"]
        if "Springfield" in page_text:
            result = ExtractionResult(
                claims=[
                    {
                        "field": "profile.description",
                        "statement": "Acme makes widgets.",
                        "value": {},
                        "relevance": 0.9,
                    },
                    {
                        "field": "profile.hq_founded",
                        "statement": "Founded 2010 in Springfield.",
                        "value": {},
                        "relevance": 0.9,
                    },
                    {
                        "field": "profile.employees",
                        "statement": "About 50 employees.",
                        "value": {},
                        "relevance": 0.9,
                    },
                    {
                        "field": "offering.products",
                        "statement": "Widgets.",
                        "value": {},
                        "relevance": 0.9,
                    },
                    {
                        "field": "customers.segments",
                        "statement": "Small businesses.",
                        "value": {},
                        "relevance": 0.9,
                    },
                    {
                        "field": "revenue.model",
                        "statement": "Subscriptions.",
                        "value": {},
                        "relevance": 0.9,
                    },
                    {
                        "field": "financials.revenue",
                        "statement": "$42M revenue.",
                        "value": {"amount_usd": 42_000_000},
                        "relevance": 0.9,
                    },
                ]
            )
        else:
            result = ExtractionResult(
                claims=[
                    {
                        "field": "events.recent",
                        "statement": "Closed a new funding round.",
                        "value": {},
                        "relevance": 0.9,
                    },
                ]
            )
        return _fake_llm_response(result.model_dump_json())

    if schema is ClaimVerification:
        return _fake_llm_response(
            ClaimVerification(verified=True, reason="Matches snippet.").model_dump_json()
        )

    if schema is SynthesisResult:
        # Find the events.recent claim's real index in the numbered prompt rather
        # than assuming claim query order — Claim.extracted_at is transaction-start
        # time (same for every claim inserted in one round), so insertion order
        # isn't guaranteed; the prompt text is the one source of truth here.
        prompt_text = kwargs["messages"][-1]["content"]
        events_index = next(
            int(line.split("]")[0].strip("["))
            for line in prompt_text.splitlines()
            if "(events.recent)" in line
        )
        result = SynthesisResult(
            opportunities=[
                {
                    "statement": "European expansion",
                    "derived_from": "events",
                    "source_claim_indices": [events_index],
                }
            ],
            threats=[],
            recommendations=[
                {
                    "statement": "Prioritize EU launch",
                    "rationale": "Momentum from funding round",
                    "priority": "high",
                    "source_claim_indices": [events_index],
                    "swot_indices": [0],
                }
            ],
        )
        return _fake_llm_response(result.model_dump_json())

    raise AssertionError(f"unexpected schema {schema}")


@pytest.fixture
def fetcher_mock() -> AsyncMock:
    async def fake_fetch(self: Fetcher, url: str) -> str:
        return NEWS_HTML if "news" in url else OVERVIEW_HTML

    return fake_fetch


@pytest.mark.asyncio
async def test_full_graph_run_produces_cited_report(fetcher_mock: AsyncMock) -> None:
    company_domain = f"https://acme-{uuid4().hex[:8]}.example.com/"
    created_company_id = None
    created_report_id = None

    try:
        async with SessionFactory() as session:
            user = await get_or_create_cli_user(session)
            company = await get_or_create_company(session, name="Acme Corp", domain=company_domain)
            created_company_id = company.id
            report = await create_report(
                session, user_id=user.id, target_company_id=company.id, tier="quick"
            )
            created_report_id = report.id
            await session.commit()

            budget = Budget(limit_usd=1.0)
            gateway = LLMGateway()
            fetcher = Fetcher()
            http_client = httpx.AsyncClient()

            with (
                patch.object(Fetcher, "fetch", fetcher_mock),
                patch(
                    "fieldbrief.agents.nodes.search",
                    new=AsyncMock(
                        return_value=[
                            SearchResult(
                                title="Acme news", url=f"{company_domain}news", snippet=None
                            )
                        ]
                    ),
                ),
                patch(
                    "fieldbrief.llm.gateway.litellm.acompletion",
                    new=AsyncMock(side_effect=_route_llm_call),
                ),
                patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
            ):
                graph = build_graph(
                    session=session,
                    gateway=gateway,
                    budget=budget,
                    fetcher=fetcher,
                    http_client=http_client,
                    searxng_url="http://unused.invalid",
                    tier="quick",
                )
                result = await graph.ainvoke(
                    {
                        "report_id": str(report.id),
                        "company_id": str(company.id),
                        "company_name": company.name,
                        "company_domain": company.domain,
                        "tier": "quick",
                        "retry_round": 0,
                        "gaps": [],
                        "claim_ids": [],
                        "company_snapshot": None,
                        "report_json": None,
                    }
                )
            await http_client.aclose()
            await fetcher.aclose()

        report_json = result["report_json"]
        assert report_json["companies"][0]["name"] == "Acme Corp"
        assert report_json["companies"][0]["description"]["claim_ids"]
        assert report_json["swot"]["opportunities"]
        assert report_json["recommendations"]
        assert report_json["recommendations"][0]["swot_refs"] == [0]
        assert len(result["claim_ids"]) == 8  # 7 overview + 1 news

        async with SessionFactory() as verify_session:
            db_report = await verify_session.get(Report, report.id)
            assert db_report is not None
            assert db_report.status == "done"
            assert db_report.report_json is not None

            linked = await verify_session.execute(
                select(ReportClaim).where(ReportClaim.report_id == report.id)
            )
            assert len(list(linked.scalars().all())) == 8
    finally:
        async with SessionFactory() as cleanup_session:
            if created_report_id is not None:
                await cleanup_session.execute(
                    delete(ReportClaim).where(ReportClaim.report_id == created_report_id)
                )
                await cleanup_session.execute(delete(Report).where(Report.id == created_report_id))
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
                await cleanup_session.execute(
                    delete(Company).where(Company.id == created_company_id)
                )
            await cleanup_session.commit()
        await engine.dispose()
