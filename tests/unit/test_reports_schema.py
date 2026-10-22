from datetime import date, datetime
from uuid import uuid4

from fieldbrief.reports.schema import (
    SWOT,
    AudienceImplications,
    Citation,
    CompanySnapshot,
    ComparisonCell,
    ComparisonRow,
    ComparisonTable,
    Confidence,
    CoverageGap,
    ExecutiveFinding,
    Freshness,
    ReportJSON,
    ReportMeta,
    SWOTItem,
    Tier,
)


def _citation() -> Citation:
    return Citation(
        claim_ids=[uuid4()], confidence=Confidence(score=0.8, corroboration=1, verified=True)
    )


def test_minimal_report_round_trips_through_json() -> None:
    target_id = uuid4()
    report = ReportJSON(
        meta=ReportMeta(
            target_company_id=target_id,
            tier=Tier.quick,
            generated_at=datetime.now(),
            data_as_of=date.today(),
        ),
        companies=[
            CompanySnapshot(
                company_id=target_id,
                name="Wix.com",
                is_target=True,
                description=_citation(),
                products=_citation(),
                customer_segments=_citation(),
                revenue_model=_citation(),
                freshness=Freshness(as_of=date.today(), stale=False),
            )
        ],
        swot=SWOT(
            opportunities=[
                SWOTItem(statement="Growing market", derived_from="events", claim_ids=[uuid4()])
            ]
        ),
        gaps=[CoverageGap(company_id=target_id, field="financials.profitability", attempts=2)],
    )

    restored = ReportJSON.model_validate_json(report.model_dump_json())
    assert restored == report
    assert restored.companies[0].is_target is True
    assert restored.gaps[0].reason == "not_found"


def test_empty_sections_default_to_empty_not_missing() -> None:
    report = ReportJSON(
        meta=ReportMeta(
            target_company_id=uuid4(),
            tier=Tier.quick,
            generated_at=datetime.now(),
            data_as_of=date.today(),
        )
    )
    assert report.companies == []
    assert report.executive_brief == []
    assert report.five_forces.forces == []
    assert report.vrio == []
    assert report.comparison.rows == []
    assert report.recommendations == []
    assert report.audience_implications is None
    assert report.gaps == []
    assert report.meta.audience == "analyst"


def test_report_carries_audience_executive_brief_and_comparison() -> None:
    target_id = uuid4()
    competitor_id = uuid4()
    finding_claim = uuid4()
    report = ReportJSON(
        meta=ReportMeta(
            target_company_id=target_id,
            competitor_ids=[competitor_id],
            tier=Tier.standard,
            audience="investor",
            generated_at=datetime.now(),
            data_as_of=date.today(),
        ),
        executive_brief=[
            ExecutiveFinding(
                statement="Revenue grew 40% year over year.",
                why_it_matters="Signals accelerating demand ahead of a funding round.",
                claim_ids=[finding_claim],
            )
        ],
        comparison=ComparisonTable(
            rows=[
                ComparisonRow(
                    dimension="revenue",
                    label="Revenue",
                    cells=[
                        ComparisonCell(company_id=target_id, citation=_citation()),
                        ComparisonCell(company_id=competitor_id, citation=None),
                    ],
                )
            ]
        ),
        audience_implications=AudienceImplications(
            audience="investor",
            relevance="Growth rate matters for the investment thesis.",
            assumptions=["Growth rate is sustained, not a one-time spike."],
            follow_up_questions=["What drove the growth — new customers or expansion?"],
            claim_ids=[finding_claim],
        ),
    )

    restored = ReportJSON.model_validate_json(report.model_dump_json())
    assert restored == report
    assert restored.meta.audience == "investor"
    assert restored.comparison.rows[0].cells[1].citation is None  # a clean gap, not an error
    assert restored.audience_implications is not None
    assert restored.audience_implications.audience == "investor"
