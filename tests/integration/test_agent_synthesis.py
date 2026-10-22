"""agents/synthesis.py coverage for the framework-correctness fixes (ADR
0013): VRIO organization affecting the implication, insufficient-evidence
Five Forces ratings, the positioning evidence gate, and the
other_strengths/other_weaknesses SWOT path. Real Postgres for claims/
sources (never committed — each test rolls back), LLM calls mocked.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.agents.llm_schemas import FrameworkSynthesisResult
from fieldbrief.agents.synthesis import CompanyRef, _vrio_implication, run_synthesis
from fieldbrief.db.repositories import add_claim, get_or_create_company, get_or_create_source
from fieldbrief.db.session import SessionFactory, engine
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    async with SessionFactory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


def _fake_response(parsed_json: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=parsed_json))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def test_vrio_implication_table_includes_unexploited_advantage() -> None:
    """ADR 0013 supersedes ADR 0009's "organized doesn't change the
    implication" clause: a valuable+rare+inimitable resource the company
    isn't organized to capture is its own bucket, not sustained_advantage."""
    assert _vrio_implication(False, False, False, True) == "competitive_disadvantage"
    assert _vrio_implication(True, False, False, True) == "parity"
    assert _vrio_implication(True, True, False, True) == "temporary_advantage"
    assert _vrio_implication(True, True, True, False) == "unexploited_advantage"
    assert _vrio_implication(True, True, True, True) == "sustained_advantage"


async def _one_claim_company(session: AsyncSession, *, field: str, value: dict) -> tuple:
    domain = f"https://synth-{uuid.uuid4().hex[:8]}.example.com/"
    company = await get_or_create_company(session, name="Synth Test Co", domain=domain)
    source, _ = await get_or_create_source(session, url=domain, content_hash="h1")
    claim = await add_claim(
        session,
        company_id=company.id,
        source_id=source.id,
        field=field,
        statement="test claim",
        value=value,
        snippet="",
        relevance=0.9,
        verified=True,
    )
    return company, claim


@pytest.mark.asyncio
async def test_unexploited_advantage_rolls_up_to_strengths(session: AsyncSession) -> None:
    company, _claim = await _one_claim_company(
        session, field="resources.advantages", value={"resource": "patent"}
    )
    result = FrameworkSynthesisResult(
        vrio=[
            {
                "resource": "patent",
                "valuable": True,
                "rare": True,
                "inimitable": True,
                "organized": False,
                "rationale": "Has it, not capturing it.",
                "source_claim_indices": [0],
            }
        ],
    )
    with (
        patch(
            "fieldbrief.llm.gateway.litellm.acompletion",
            new=AsyncMock(return_value=_fake_response(result.model_dump_json())),
        ),
        patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
    ):
        report_json = await run_synthesis(
            session=session,
            gateway=LLMGateway(),
            budget=Budget(limit_usd=5.0),
            report_id=uuid.uuid4(),
            tier="standard",
            companies=[
                CompanyRef(company_id=company.id, name=company.name, is_target=True, snapshot={})
            ],
            gaps=[],
        )

    assert report_json["vrio"][0]["competitive_implication"] == "unexploited_advantage"
    assert any(s["derived_from"] == "vrio" for s in report_json["swot"]["strengths"])


@pytest.mark.asyncio
async def test_insufficient_evidence_force_is_omitted_from_swot(session: AsyncSession) -> None:
    company, _claim = await _one_claim_company(
        session, field="market.forces", value={"force": "rivalry"}
    )
    result = FrameworkSynthesisResult(
        five_forces=[
            {
                "force": "rivalry",
                "rating": "insufficient_evidence",
                "rationale": "Mentioned only in passing.",
                "source_claim_indices": [0],
            }
        ],
    )
    with (
        patch(
            "fieldbrief.llm.gateway.litellm.acompletion",
            new=AsyncMock(return_value=_fake_response(result.model_dump_json())),
        ),
        patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
    ):
        report_json = await run_synthesis(
            session=session,
            gateway=LLMGateway(),
            budget=Budget(limit_usd=5.0),
            report_id=uuid.uuid4(),
            tier="standard",
            companies=[
                CompanyRef(company_id=company.id, name=company.name, is_target=True, snapshot={})
            ],
            gaps=[],
        )

    assert report_json["five_forces"]["forces"][0]["rating"] == "insufficient_evidence"
    assert report_json["swot"]["opportunities"] == []
    assert report_json["swot"]["threats"] == []


@pytest.mark.asyncio
async def test_other_weaknesses_feeds_swot_from_an_internal_signal(
    session: AsyncSession,
) -> None:
    """Strengths/Weaknesses used to come only from VRIO — an internal
    capability signal (e.g. a disclosed risk) had nowhere to go."""
    company, _claim = await _one_claim_company(session, field="risks.disclosed", value={})
    result = FrameworkSynthesisResult(
        other_weaknesses=[
            {
                "statement": "Heavily dependent on a single large customer.",
                "derived_from": "risks",
                "source_claim_indices": [0],
            }
        ],
    )
    with (
        patch(
            "fieldbrief.llm.gateway.litellm.acompletion",
            new=AsyncMock(return_value=_fake_response(result.model_dump_json())),
        ),
        patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
    ):
        report_json = await run_synthesis(
            session=session,
            gateway=LLMGateway(),
            budget=Budget(limit_usd=5.0),
            report_id=uuid.uuid4(),
            tier="standard",
            companies=[
                CompanyRef(company_id=company.id, name=company.name, is_target=True, snapshot={})
            ],
            gaps=[],
        )

    assert len(report_json["swot"]["weaknesses"]) == 1
    assert report_json["swot"]["weaknesses"][0]["derived_from"] == "risks"


@pytest.mark.asyncio
async def test_positioning_omitted_when_fewer_than_two_companies_are_placed(
    session: AsyncSession,
) -> None:
    """Every other roll-up loop drops an unsupported item; positioning used
    to be the one exception. A chart with only one real placement isn't a
    comparison — omit the whole map rather than show something sparse."""
    target, _target_claim = await _one_claim_company(session, field="pricing.model", value={})
    domain_b = f"https://synth-{uuid.uuid4().hex[:8]}.example.com/"
    competitor = await get_or_create_company(session, name="Competitor Co", domain=domain_b)

    companies = [
        CompanyRef(company_id=target.id, name="Synth Test Co", is_target=True, snapshot={}),
        CompanyRef(company_id=competitor.id, name="Competitor Co", is_target=False, snapshot={}),
    ]
    # Only the target gets placed — the model had no claims for the
    # competitor's position, so it correctly omitted it entirely.
    result = FrameworkSynthesisResult(
        positioning={
            "x_axis_rationale": "Target charges a premium.",
            "y_axis_rationale": "No evidence for the competitor's breadth.",
            "companies": [{"company_index": 0, "x": 0.8, "y": 0.5, "source_claim_indices": [0]}],
        },
    )
    with (
        patch(
            "fieldbrief.llm.gateway.litellm.acompletion",
            new=AsyncMock(return_value=_fake_response(result.model_dump_json())),
        ),
        patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
    ):
        report_json = await run_synthesis(
            session=session,
            gateway=LLMGateway(),
            budget=Budget(limit_usd=5.0),
            report_id=uuid.uuid4(),
            tier="standard",
            companies=companies,
            gaps=[],
        )

    assert report_json["positioning"]["companies"] == []
    assert report_json["positioning"]["x_axis"] is None


@pytest.mark.asyncio
async def test_executive_brief_and_audience_implications_are_populated(
    session: AsyncSession,
) -> None:
    """The executive brief and audience-implications section (ADR 0014)
    ride inside the same synthesis call and claim-index resolution as
    every other section — no new LLM call, same "omit if unsupported"
    discipline."""
    company, _claim = await _one_claim_company(
        session, field="financials.revenue", value={"amount_usd": 40_000_000}
    )
    result = FrameworkSynthesisResult(
        executive_brief=[
            {
                "statement": "Revenue grew sharply year over year.",
                "why_it_matters": "Signals strong demand ahead of a funding round.",
                "source_claim_indices": [0],
            }
        ],
        audience_implications={
            "relevance": "Growth rate matters directly for an investment thesis.",
            "assumptions": ["Growth is organic, not acquisition-driven."],
            "follow_up_questions": ["Is this growth rate sustainable next year?"],
            "source_claim_indices": [0],
        },
    )
    with (
        patch(
            "fieldbrief.llm.gateway.litellm.acompletion",
            new=AsyncMock(return_value=_fake_response(result.model_dump_json())),
        ),
        patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
    ):
        report_json = await run_synthesis(
            session=session,
            gateway=LLMGateway(),
            budget=Budget(limit_usd=5.0),
            report_id=uuid.uuid4(),
            tier="standard",
            companies=[
                CompanyRef(company_id=company.id, name=company.name, is_target=True, snapshot={})
            ],
            gaps=[],
            audience="investor",
        )

    assert len(report_json["executive_brief"]) == 1
    assert report_json["executive_brief"][0]["claim_ids"]
    assert report_json["audience_implications"]["audience"] == "investor"
    assert report_json["audience_implications"]["claim_ids"]
    assert report_json["meta"]["audience"] == "investor"


@pytest.mark.asyncio
async def test_audience_implications_omitted_when_unsupported(session: AsyncSession) -> None:
    """Interpretation without a claim behind it is invented, not honest —
    omit the whole section rather than guess."""
    company, _claim = await _one_claim_company(
        session, field="financials.revenue", value={"amount_usd": 40_000_000}
    )
    result = FrameworkSynthesisResult(audience_implications=None)
    with (
        patch(
            "fieldbrief.llm.gateway.litellm.acompletion",
            new=AsyncMock(return_value=_fake_response(result.model_dump_json())),
        ),
        patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
    ):
        report_json = await run_synthesis(
            session=session,
            gateway=LLMGateway(),
            budget=Budget(limit_usd=5.0),
            report_id=uuid.uuid4(),
            tier="standard",
            companies=[
                CompanyRef(company_id=company.id, name=company.name, is_target=True, snapshot={})
            ],
            gaps=[],
        )

    assert report_json["audience_implications"] is None
    assert report_json["executive_brief"] == []
    assert report_json["meta"]["audience"] == "analyst"  # default when not specified


def test_comparison_table_includes_populated_dimensions_and_drops_empty_ones() -> None:
    """Deterministic reshape of existing CompanySnapshot data (ADR 0014) —
    no new research, no new LLM call."""
    from fieldbrief.agents.synthesis import _build_comparison_table

    target_id = uuid.uuid4()
    competitor_id = uuid.uuid4()
    revenue_citation = {
        "claim_ids": [str(uuid.uuid4())],
        "confidence": {"score": 0.8, "corroboration": 1, "verified": True, "status": "supported"},
    }
    companies = [
        CompanyRef(
            company_id=target_id,
            name="Target Co",
            is_target=True,
            snapshot={"revenue": revenue_citation, "description": None, "products": None},
        ),
        CompanyRef(
            company_id=competitor_id,
            name="Competitor Co",
            is_target=False,
            snapshot={"revenue": None, "description": None, "products": None},
        ),
    ]

    table = _build_comparison_table(companies)

    dimensions = {row["dimension"] for row in table["rows"]}
    assert "revenue" in dimensions  # at least one company has a value
    assert "description" not in dimensions  # no company has a value — dropped, not shown empty
    revenue_row = next(row for row in table["rows"] if row["dimension"] == "revenue")
    assert revenue_row["cells"][0]["citation"] == revenue_citation
    assert revenue_row["cells"][1]["citation"] is None  # a clean gap, not an error
