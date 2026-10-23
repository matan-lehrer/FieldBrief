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
                "funding": None,
                "market_cap_or_valuation": None,
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
            "publisher": "acme.com",
            "published_at": None,
        }
    }

    html = render_report_html(report, claims, "Acme Corp")

    assert "Acme Corp" in html
    assert "Acme makes widgets." in html
    assert "Revenue (not found)" in html  # the gap, as a human label, not the raw field key
    assert "{{" not in html and "{%" not in html


def test_render_report_html_has_source_popover_linking_to_the_origin() -> None:
    """Sources are a hover/click trigger, not an always-visible list — but
    the actual finding text is always on the page regardless; the trigger
    only ever reveals a plain link to the source, never the only copy of
    the answer, and never a quoted excerpt."""
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    claims = {
        CID1: {
            "statement": "Acme makes widgets.",
            "source_url": "https://acme.com",
            "source_type": "official",
            "publisher": "acme.com",
            "published_at": None,
        }
    }

    html = render_report_html(report, claims, "Acme Corp")

    assert '<section id="snapshot">' in html
    assert "1 source" in html  # the popover trigger, not an always-visible list
    assert "Acme makes widgets." in html  # the finding itself, directly on the page
    assert 'href="https://acme.com"' in html  # a plain link to the origin
    assert "acme.com" in html


def test_sources_popover_never_shows_the_statement_as_a_quote() -> None:
    """A claim's own generated statement is never presented as if it were a
    verbatim source quotation — the popover shows only the publisher,
    date, and a link, never a quote."""
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    claims = {
        CID1: {
            "statement": "Acme makes widgets.",
            "source_url": "https://acme.com",
            "source_type": "official",
            "publisher": "Acme Newsroom",
            "published_at": "2026-01-15",
        }
    }

    html = render_report_html(report, claims, "Acme Corp")

    assert "&ldquo;" not in html and "&rdquo;" not in html
    assert "Acme Newsroom" in html
    assert "2026-01-15" in html


def test_company_snapshot_shows_the_entity_scope_next_to_a_financial_figure() -> None:
    """A revenue/funding/valuation figure must say what it's actually about —
    the Synamedia regression: a subsidiary's revenue was silently compared
    against the parent's headcount with no indication they were different
    scopes. The scope label makes that visible on the page."""
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    report["companies"][0]["revenue"] = _citation(CID1)
    claims = {
        CID1: {
            "statement": "Revenue was £0.5M.",
            "source_url": "https://find-and-update.company-information.service.gov.uk/x",
            "source_type": "filing",
            "publisher": "Companies House",
            "published_at": None,
            "scope_label": "Synamedia Holdings Limited · standalone · FY2023 · GBP",
        }
    }

    html = render_report_html(report, claims, "Synamedia")

    assert "Synamedia Holdings Limited · standalone · FY2023 · GBP" in html


def test_different_scope_confidence_status_renders_distinctly_from_conflicting() -> None:
    """Two figures for incompatible entities/periods aren't a 'conflict' —
    they're different facts that happen to share a field name, so they get
    their own status rather than being lumped in with real disagreements."""
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    report["companies"][0]["revenue"] = {
        "claim_ids": [CID1],
        "confidence": {
            "score": 0.5,
            "corroboration": 2,
            "verified": True,
            "status": "different_scope",
        },
    }
    claims = {
        CID1: {
            "statement": "Revenue was £0.5M (standalone).",
            "source_url": "https://find-and-update.company-information.service.gov.uk/x",
            "source_type": "filing",
            "publisher": "Companies House",
            "published_at": None,
            "scope_label": None,
        }
    }

    html = render_report_html(report, claims, "Synamedia")

    assert "status-different_scope" in html
    assert "different scope" in html


def test_render_report_html_shows_not_found_for_missing_citation() -> None:
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    report["companies"][0]["products"] = None

    html = render_report_html(report, {}, "Acme Corp")

    assert "not found" in html


def test_render_pdf_html_has_content_parity_with_the_web_report() -> None:
    """The PDF used to be a deliberately short "highlights only" template
    with no citations at all — it now gets the same report_json and
    resolved claims as the web view, just a print-appropriate layout with
    numbered source references instead of hover."""
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    claims = {
        CID1: {
            "statement": "Acme makes widgets for small businesses.",
            "source_url": "https://acme.com",
            "source_type": "official",
            "publisher": "Acme Newsroom",
            "published_at": "2026-01-15",
        }
    }

    html = render_pdf_html(report, claims, "Acme Corp")

    assert "Acme Corp" in html
    assert "Acme makes widgets for small businesses." in html
    assert "Revenue (not found)" in html  # the gap, same as the web report
    assert "[1]" in html  # a numbered source reference, print has no hover
    assert "Acme Newsroom" in html  # the Sources list at the end
    assert "{{" not in html and "{%" not in html


def test_company_introduction_renders_before_the_executive_brief() -> None:
    """Readers should understand the business before seeing strategic
    conclusions — the plain-English intro comes first."""
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    report["company_introductions"] = [
        {
            "company_id": target_id,
            "intro": "Acme sells widgets to small businesses across the region.",
            "claim_ids": [CID1],
        }
    ]
    report["executive_brief"] = [
        {"statement": "Revenue grew.", "why_it_matters": "Signals demand.", "claim_ids": [CID1]}
    ]
    claims = {
        CID1: {
            "statement": "Acme makes widgets.",
            "source_url": "https://acme.com",
            "source_type": "official",
            "publisher": "acme.com",
            "published_at": None,
        }
    }

    html = render_report_html(report, claims, "Acme Corp")

    intro_pos = html.index("Acme sells widgets to small businesses")
    brief_pos = html.index('<section id="brief">')
    assert intro_pos < brief_pos


def test_recommendation_shows_its_own_reasoning_chain_or_says_why_there_is_none() -> None:
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    report["recommendations"] = [
        {
            "statement": "Double down on the patented process.",
            "rationale": "It's a sustained advantage.",
            "action": "File two defensive continuation patents.",
            "expected_benefit": "Extends the moat's effective lifespan.",
            "key_assumption": "Competitors haven't already designed around it.",
            "next_validation_step": "Run a freedom-to-operate search.",
            "priority": "high",
            "claim_ids": [CID1],
        }
    ]

    html = render_report_html(report, {}, "Acme Corp")

    assert "File two defensive continuation patents." in html
    assert "Run a freedom-to-operate search." in html

    report["recommendations"] = []
    html_empty = render_report_html(report, {}, "Acme Corp")
    assert "Insufficient evidence to recommend action" in html_empty


def test_executive_brief_finding_with_an_overreach_flag_shows_a_caveat() -> None:
    """A finding resting only on interpretation/hypothesis claims must show
    a visible caveat, not sit at the same confidence as a well-evidenced one."""
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    report["meta"]["overreach_flags"] = [
        {"section": "executive_brief", "statement": "Speculative finding."}
    ]
    report["executive_brief"] = [
        {
            "statement": "Speculative finding.",
            "why_it_matters": "Worth watching.",
            "claim_ids": [CID1],
        }
    ]
    claims = {
        CID1: {
            "statement": "Analyst speculation.",
            "source_url": "https://acme.com",
            "source_type": "news",
            "publisher": "acme.com",
            "published_at": None,
        }
    }

    html = render_report_html(report, claims, "Acme Corp")

    assert "overreach-caveat" in html
    assert "limited evidence" in html


def test_research_summary_section_shows_sources_and_limitations() -> None:
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    report["meta"]["research_summary"] = {
        "source_type_counts": {"official": 2, "news": 1},
        "limitations": ["1 field(s) could not be found"],
    }

    html = render_report_html(report, {}, "Acme Corp")

    assert "2 official" in html
    assert "1 news" in html
    assert "1 field(s) could not be found" in html


def test_evidence_badge_has_a_plain_english_tooltip_not_a_certainty_claim() -> None:
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    report["companies"][0]["revenue"] = _citation(CID1)
    claims = {
        CID1: {
            "statement": "Revenue was $10M.",
            "source_url": "https://acme.com",
            "source_type": "official",
            "publisher": "acme.com",
            "published_at": None,
        }
    }

    html = render_report_html(report, claims, "Acme Corp")

    assert "Backed by a verified source, not yet independently corroborated" in html
    assert "99%" not in html and "100% confidence" not in html


def test_gap_label_is_human_readable_not_a_raw_field_key() -> None:
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)

    html = render_report_html(report, {}, "Acme Corp")

    assert "Revenue (not found)" in html
    assert "financials.revenue" not in html


def test_no_competitors_found_shows_an_incomplete_coverage_banner_not_a_fake_comparison() -> None:
    """Zero confirmed competitors must read as "research incomplete," never
    as a silent single-company table presented like a real comparison."""
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    report["meta"]["competitor_coverage"] = "none"
    report["comparison"] = {
        "rows": [
            {
                "dimension": "revenue",
                "label": "Revenue",
                "cells": [{"company_id": target_id, "citation": None}],
            }
        ]
    }

    html = render_report_html(report, {}, "Acme Corp")

    assert "Competitor research incomplete" in html
    assert '<section id="comparison">' not in html


def test_render_pdf_html_vrio_table_repeats_its_header_across_page_breaks() -> None:
    """A long VRIO/comparison table must not lose its header when it spans
    a page break in print -- <thead> plus break-inside: avoid on rows."""
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    report["vrio"] = [
        {
            "resource": "Delivery network",
            "valuable": True,
            "rare": True,
            "inimitable": True,
            "organized": True,
            "competitive_implication": "sustained_advantage",
            "rationale": "Hard to replicate.",
            "claim_ids": [],
        }
    ]

    html = render_pdf_html(report, {}, "Acme Corp")

    assert "<thead><tr><th>Resource</th>" in html


def test_render_pdf_html_handles_a_report_with_no_resolvable_claims() -> None:
    target_id = str(uuid.uuid4())
    report = _minimal_report(target_id)
    report["companies"][0]["description"] = None

    html = render_pdf_html(report, {}, "Acme Corp")

    assert "not found" in html
