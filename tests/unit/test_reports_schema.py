from datetime import date, datetime
from uuid import uuid4

from fieldbrief.reports.schema import (
    SWOT,
    Citation,
    CompanySnapshot,
    Confidence,
    CoverageGap,
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
    assert report.five_forces.forces == []
    assert report.vrio == []
    assert report.recommendations == []
    assert report.gaps == []
