from evals.harness import CompanyEvalResult, score_report, write_results_table
from evals.schema import GoldenCompany


def _golden(known_gaps: list[str] | None = None) -> GoldenCompany:
    return GoldenCompany(name="Acme", public=False, industry="widgets", known_gaps=known_gaps or [])


def _report(findings_with_claims: int, findings_without_claims: int, gaps: list[dict]) -> dict:
    cited = [{"statement": "x", "claim_ids": ["id"]} for _ in range(findings_with_claims)]
    uncited = [{"statement": "y", "claim_ids": []} for _ in range(findings_without_claims)]
    return {
        "swot": {"strengths": cited, "weaknesses": [], "opportunities": uncited, "threats": []},
        "recommendations": [],
        "five_forces": {"forces": []},
        "vrio": [],
        "gaps": gaps,
    }


def test_score_report_counts_cited_vs_uncited_findings() -> None:
    report = _report(findings_with_claims=3, findings_without_claims=1, gaps=[])
    total, cited, gaps_total, _gaps_not_found, invented = score_report(report, _golden())
    assert total == 4
    assert cited == 3
    assert gaps_total == 0
    assert invented == []


def test_score_report_flags_invented_known_gaps() -> None:
    golden = _golden(known_gaps=["financials.profitability"])
    report = _report(0, 0, gaps=[])  # the known gap never shows up as a gap -> invented

    _, _, _, _, invented = score_report(report, golden)

    assert invented == ["financials.profitability"]


def test_score_report_does_not_flag_known_gaps_that_are_present() -> None:
    golden = _golden(known_gaps=["financials.profitability"])
    report = _report(
        0, 0, gaps=[{"field": "financials.profitability", "reason": "not_found", "attempts": 2}]
    )

    _, _, _, gaps_not_found, invented = score_report(report, golden)

    assert invented == []
    assert gaps_not_found == 1


def test_citation_validity_is_one_when_no_findings() -> None:
    result = CompanyEvalResult(slug="acme", status="done")
    assert result.citation_validity == 1.0


def test_citation_validity_ratio() -> None:
    result = CompanyEvalResult(slug="acme", status="done", findings_total=4, findings_cited=3)
    assert result.citation_validity == 0.75


def test_write_results_table_includes_summary_stats(tmp_path, monkeypatch) -> None:
    import evals.harness as harness_module

    monkeypatch.setattr(harness_module, "RESULTS_DIR", tmp_path)
    results = [
        CompanyEvalResult(
            slug="acme",
            status="done",
            cost_usd=0.1,
            latency_s=5.0,
            findings_total=2,
            findings_cited=2,
        ),
        CompanyEvalResult(slug="beta", status="failed", error="boom"),
    ]

    out_path = write_results_table(results)

    content = out_path.read_text()
    assert "acme" in content
    assert "beta" in content
    assert "Completion rate:** 1/2" in content
