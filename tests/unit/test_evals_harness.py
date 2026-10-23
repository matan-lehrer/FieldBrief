from evals.harness import CompanyEvalResult, score_report, write_results_table
from evals.schema import GoldenCompany


def _golden(known_gaps: list[str] | None = None) -> GoldenCompany:
    return GoldenCompany(name="Acme", public=False, industry="widgets", known_gaps=known_gaps or [])


def _report(findings_with_claims: int, findings_without_claims: int, gaps: list[dict]) -> dict:
    cited = [{"statement": "x", "claim_ids": ["id"]} for _ in range(findings_with_claims)]
    uncited = [{"statement": "y", "claim_ids": []} for _ in range(findings_without_claims)]
    return {
        "meta": {"competitor_coverage": "full"},
        "companies": [],
        "swot": {"strengths": cited, "weaknesses": [], "opportunities": uncited, "threats": []},
        "recommendations": [],
        "five_forces": {"forces": []},
        "vrio": [],
        "gaps": gaps,
    }


def test_score_report_counts_cited_vs_uncited_findings() -> None:
    report = _report(findings_with_claims=3, findings_without_claims=1, gaps=[])
    score = score_report(report, _golden())
    assert score.findings_total == 4
    assert score.findings_cited == 3
    assert score.gaps_total == 0
    assert score.invented_known_gaps == []


def test_score_report_flags_invented_known_gaps() -> None:
    golden = _golden(known_gaps=["financials.profitability"])
    report = _report(0, 0, gaps=[])  # the known gap never shows up as a gap -> invented

    score = score_report(report, golden)

    assert score.invented_known_gaps == ["financials.profitability"]


def test_score_report_does_not_flag_known_gaps_that_are_present() -> None:
    golden = _golden(known_gaps=["financials.profitability"])
    report = _report(
        0, 0, gaps=[{"field": "financials.profitability", "reason": "not_found", "attempts": 2}]
    )

    score = score_report(report, golden)

    assert score.invented_known_gaps == []
    assert score.gaps_not_found == 1


def test_score_report_matches_key_points_by_keyword_overlap() -> None:
    golden = GoldenCompany(
        name="Acme",
        public=False,
        industry="widgets",
        key_points={"five_forces": ["High rivalry from Beta Corp and Gamma Inc"]},
    )
    report = _report(0, 0, gaps=[])
    report["five_forces"] = {
        "forces": [
            {
                "force": "rivalry",
                "rating": "high",
                "rationale": "High rivalry from Beta Corp in this market.",
                "claim_ids": ["id"],
            }
        ]
    }

    score = score_report(report, golden)

    assert score.key_points_total == 1
    assert score.key_points_matched == 1


def test_score_report_flags_an_entity_scope_conflict() -> None:
    report = _report(0, 0, gaps=[])
    report["companies"] = [
        {
            "revenue": {
                "claim_ids": ["a", "b"],
                "confidence": {
                    "score": 0.5,
                    "corroboration": 2,
                    "verified": True,
                    "status": "conflicting",
                },
            },
        }
    ]

    score = score_report(report, _golden())

    assert score.entity_scope_conflicts == 1


def test_score_report_flags_incomplete_recommendations() -> None:
    report = _report(0, 0, gaps=[])
    report["recommendations"] = [
        {
            "statement": "x",
            "rationale": "y",
            "action": "",
            "expected_benefit": "z",
            "key_assumption": "w",
            "next_validation_step": "v",
            "priority": "low",
            "claim_ids": ["id"],
        }
    ]

    score = score_report(report, _golden())

    assert score.recommendations_complete is False


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
