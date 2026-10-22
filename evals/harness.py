"""Eval harness (ADR 0011): runs the golden set, computes claim precision,
citation validity, cost, and latency, writes a committed results table.

Not part of src/fieldbrief — CLAUDE.md: "Quality is measured by the eval
harness, not by unit tests." Run with:
    uv run python evals/harness.py [--repeat N]
Needs LLM_API_KEY set and `docker compose up -d postgres` — this hits a real
LLM and real network, unlike anything under tests/.
"""

import argparse
import asyncio
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import yaml

from evals.schema import GoldenCompany
from fieldbrief.agents.field_checklist import (
    TIER_COMPETITOR_COUNT,
    budget_usd_for,
    time_budget_seconds_for,
)
from fieldbrief.agents.orchestrator import run_report
from fieldbrief.config import settings
from fieldbrief.db.repositories import create_report, get_or_create_cli_user, get_or_create_company
from fieldbrief.db.session import SessionFactory
from fieldbrief.fetch.http import DEFAULT_USER_AGENT, Fetcher
from fieldbrief.fetch.wikidata import get_company_details, pick_company_like, search_candidates
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway

GOLDEN_DIR = Path(__file__).parent / "golden"
RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class CompanyEvalResult:
    slug: str
    status: str
    cost_usd: float = 0.0
    latency_s: float = 0.0
    findings_total: int = 0
    findings_cited: int = 0
    gaps_total: int = 0
    gaps_not_found: int = 0
    invented_known_gaps: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def citation_validity(self) -> float:
        return self.findings_cited / self.findings_total if self.findings_total else 1.0


def _report_findings(report_json: dict[str, Any]) -> list[dict[str, Any]]:
    swot = report_json["swot"]
    findings: list[dict[str, Any]] = [
        *swot["strengths"],
        *swot["weaknesses"],
        *swot["opportunities"],
        *swot["threats"],
        *report_json["recommendations"],
        *report_json["five_forces"]["forces"],
        *report_json["vrio"],
    ]
    return findings


def score_report(
    report_json: dict[str, Any], golden: GoldenCompany
) -> tuple[int, int, int, int, list[str]]:
    """Returns (findings_total, findings_cited, gaps_total, gaps_not_found, invented_known_gaps)."""
    findings = _report_findings(report_json)
    findings_cited = sum(1 for f in findings if f.get("claim_ids"))

    gaps = report_json["gaps"]
    gaps_not_found = sum(1 for g in gaps if g["reason"] == "not_found")

    gap_fields = {g["field"] for g in gaps}
    invented_known_gaps = [field for field in golden.known_gaps if field not in gap_fields]

    return len(findings), findings_cited, len(gaps), gaps_not_found, invented_known_gaps


async def run_one(slug: str, golden: GoldenCompany) -> CompanyEvalResult:
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(headers={"User-Agent": DEFAULT_USER_AGENT}) as wikidata_client:
            candidates = await search_candidates(wikidata_client, golden.name, limit=5)
            if not candidates:
                return CompanyEvalResult(
                    slug=slug, status="resolve_failed", error="no Wikidata candidates"
                )
            best = await pick_company_like(wikidata_client, candidates) or candidates[0]
            details = await get_company_details(wikidata_client, best.qid)

            budget = Budget(limit_usd=budget_usd_for("standard"))
            gateway = LLMGateway()
            fetcher = Fetcher()
            http_client = httpx.AsyncClient()
            try:
                async with SessionFactory() as session:
                    user = await get_or_create_cli_user(session)
                    company = await get_or_create_company(
                        session,
                        name=details.name,
                        domain=details.domain,
                        aliases=details.aliases,
                        ticker=details.ticker,
                        cik=details.cik,
                        industry=details.industry,
                    )
                    report = await create_report(
                        session, user_id=user.id, target_company_id=company.id, tier="standard"
                    )
                    await session.commit()

                    report_json = await run_report(
                        session=session,
                        gateway=gateway,
                        budget=budget,
                        fetcher=fetcher,
                        http_client=http_client,
                        wikidata_client=wikidata_client,
                        searxng_url=settings.searxng_url,
                        report_id=report.id,
                        target_company=company,
                        tier="standard",
                        competitor_count=TIER_COMPETITOR_COUNT["standard"],
                        time_budget_seconds=time_budget_seconds_for("standard"),
                    )
            finally:
                await fetcher.aclose()
                await http_client.aclose()
    except Exception as exc:
        return CompanyEvalResult(
            slug=slug, status="failed", latency_s=time.monotonic() - start, error=str(exc)
        )

    latency_s = time.monotonic() - start
    findings_total, findings_cited, gaps_total, gaps_not_found, invented = score_report(
        report_json, golden
    )
    return CompanyEvalResult(
        slug=slug,
        status="done",
        cost_usd=budget.spent_usd,
        latency_s=latency_s,
        findings_total=findings_total,
        findings_cited=findings_cited,
        gaps_total=gaps_total,
        gaps_not_found=gaps_not_found,
        invented_known_gaps=invented,
    )


def write_results_table(results: list[CompanyEvalResult]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{date.today().isoformat()}.md"

    lines = [
        f"# Eval results — {date.today().isoformat()}",
        "",
        "| Company | Status | Cost ($) | Latency (s) | Citation validity | Gaps | Invented gaps |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.slug} | {r.status} | {r.cost_usd:.4f} | {r.latency_s:.1f} | "
            f"{r.citation_validity:.0%} ({r.findings_cited}/{r.findings_total}) | "
            f"{r.gaps_not_found}/{r.gaps_total} | {', '.join(r.invented_known_gaps) or '-'} |"
        )

    done = [r for r in results if r.status == "done"]
    if done:
        avg_cost = sum(r.cost_usd for r in done) / len(done)
        avg_latency = sum(r.latency_s for r in done) / len(done)
        avg_citation = sum(r.citation_validity for r in done) / len(done)
        lines += [
            "",
            f"**Completion rate:** {len(done)}/{len(results)} "
            f"({len(done) / len(results):.0%}) — design.md §1 target >=99%/95%",
            f"**Avg cost:** ${avg_cost:.4f} (target <=$0.50, standard tier)",
            f"**Avg latency:** {avg_latency:.1f}s (target <=300s p95, standard tier)",
            f"**Avg citation validity:** {avg_citation:.0%} (proxy for the >=90% accuracy NFR)",
        ]

    out_path.write_text("\n".join(lines) + "\n")
    return out_path


async def main(repeat: int) -> None:
    golden_paths = sorted(GOLDEN_DIR.glob("*.yaml"))
    if not golden_paths:
        raise SystemExit(f"No golden set files found in {GOLDEN_DIR}")

    results: list[CompanyEvalResult] = []
    for path in golden_paths:
        golden = GoldenCompany.model_validate(yaml.safe_load(path.read_text()))
        for _ in range(repeat):
            print(f"Running {path.stem}...")
            result = await run_one(path.stem, golden)
            cost, latency = result.cost_usd, result.latency_s
            print(f"  -> {result.status}, cost=${cost:.4f}, latency={latency:.1f}s")
            results.append(result)

    out_path = write_results_table(results)
    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Runs per company (ADR 0011's reliability metric wants 3)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.repeat))
