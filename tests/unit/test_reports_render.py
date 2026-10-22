import uuid

from fieldbrief.reports.render import collect_claim_ids, render_pdf_html, render_report_html

CID1 = str(uuid.uuid4())
CID2 = str(uuid.uuid4())


def _citation(cid: str) -> dict:
    return {"claim_ids": [cid], "confidence": {"score": 0.9, "corroboration": 1, "verified": True}}


def _minimal_report(target_id: str) -> dict:
    return {
        "meta": {
            "tier": "quick",
            "data_as_of": "2026-10-22",
            "generated_at": "2026-10-22T10:00:00",
        },
        "companies": [
            {
                "company_id": target_id,
                "name": "Acme Corp",
                "is_target": True,
                "hq": "Springfield",
                "founded": 2010,
                "description": _citation(CID1),
                "products": None,
                "customer_segments": None,
                "revenue_model": None,
                "revenue": None,
                "profitability": None,
                "funding_or_cap": None,
                "recent_events": None,
                "employees": None,
            }
        ],
        "five_forces": {"forces": []},
        "bmc": {"blocks": []},
        "vrio": [],
        "positioning": {"x_axis": None, "y_axis": None, "companies": []},
        "swot": {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []},
        "recommendations": [],
        "gaps": [
            {
                "company_id": target_id,
                "field": "financials.revenue",
                "reason": "not_found",
                "attempts": 2,
            }
        ],
    }


def test_collect_claim_ids_finds_company_snapshot_citations() -> None:
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    assert collect_claim_ids(report) == {CID1}


def test_collect_claim_ids_covers_every_section() -> None:
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    report["five_forces"]["forces"].append(
        {"force": "rivalry", "rating": "high", "rationale": "x", "claim_ids": [CID2]}
    )
    report["swot"]["threats"].append(
        {"statement": "x", "derived_from": "five_forces", "claim_ids": [CID1]}
    )
    ids = collect_claim_ids(report)
    assert CID1 in ids
    assert CID2 in ids


def test_render_report_html_includes_company_name_and_gap() -> None:
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    claims = {
        CID1: {
            "statement": "Acme makes widgets.",
            "source_url": "https://acme.com",
            "source_type": "official",
        }
    }

    html = render_report_html(report, claims, "Acme Corp")

    assert "Acme Corp" in html
    assert "Acme makes widgets." in html
    assert "financials.revenue" in html
    assert "{{" not in html and "{%" not in html


def test_render_report_html_has_section_nav_and_source_popover() -> None:
    """UX overhaul: sources are a hover/click trigger, not an always-visible
    list, and each present section gets a sticky nav anchor."""
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    claims = {
        CID1: {
            "statement": "Acme makes widgets.",
            "source_url": "https://acme.com",
            "source_type": "official",
        }
    }

    html = render_report_html(report, claims, "Acme Corp")

    assert '<section id="snapshot">' in html
    assert 'href="#snapshot"' in html
    assert "1 source" in html  # the popover trigger, not an always-visible list
    assert "Acme makes widgets." in html  # still in the DOM, inside the popover


def test_render_report_html_shows_not_found_for_missing_citation() -> None:
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    report["companies"][0]["products"] = None

    html = render_report_html(report, {}, "Acme Corp")

    assert "not found" in html


def test_render_pdf_html_includes_description_when_given() -> None:
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)

    html = render_pdf_html(report, "Acme Corp", "Acme makes widgets for small businesses.")

    assert "Acme makes widgets for small businesses." in html


def test_render_pdf_html_omits_description_block_when_none() -> None:
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)

    html = render_pdf_html(report, "Acme Corp", None)

    assert '<p class="desc">' not in html
