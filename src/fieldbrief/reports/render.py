"""Renders report_json into HTML (Jinja2) — the Report UI (stage 7).

report_json only stores claim_ids (design.md §4/§5: claims are the source of
truth, report_json just references them) — rendering needs the actual claim
text + source URL, so this resolves every claim_id referenced anywhere in the
report with one batched query before handing off to the template.
"""

import uuid
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.db.models import Claim, Source

TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
)

_SNAPSHOT_CITATION_FIELDS = (
    "description",
    "products",
    "customer_segments",
    "revenue_model",
    "revenue",
    "profitability",
    "funding_or_cap",
    "recent_events",
    "employees",
)


def collect_claim_ids(report_json: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for company in report_json.get("companies", []):
        for key in _SNAPSHOT_CITATION_FIELDS:
            citation = company.get(key)
            if citation:
                ids.update(citation["claim_ids"])
    for force in report_json.get("five_forces", {}).get("forces", []):
        ids.update(force["claim_ids"])
    for block in report_json.get("bmc", {}).get("blocks", []):
        ids.update(block["claim_ids"])
    for resource in report_json.get("vrio", []):
        ids.update(resource["claim_ids"])
    for placed in report_json.get("positioning", {}).get("companies", []):
        ids.update(placed["claim_ids"])
    for items in report_json.get("swot", {}).values():
        for item in items:
            ids.update(item["claim_ids"])
    for rec in report_json.get("recommendations", []):
        ids.update(rec["claim_ids"])
    return ids


async def build_claims_map(session: AsyncSession, claim_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not claim_ids:
        return {}
    ids = [uuid.UUID(cid) for cid in claim_ids]
    result = await session.execute(select(Claim).where(Claim.id.in_(ids)))
    claims = list(result.scalars().all())
    source_ids = {c.source_id for c in claims}
    sources_result = await session.execute(select(Source).where(Source.id.in_(source_ids)))
    sources = {s.id: s for s in sources_result.scalars().all()}
    return {
        str(c.id): {
            "statement": c.statement,
            "source_url": sources[c.source_id].url,
            "source_type": sources[c.source_id].source_type,
        }
        for c in claims
        if c.source_id in sources
    }


def render_report_html(
    report_json: dict[str, Any],
    claims: dict[str, dict[str, Any]],
    company_name: str,
    report_id: uuid.UUID | None = None,
    api_key: str | None = None,
) -> str:
    template = _env.get_template("report.html")
    return template.render(
        report=report_json,
        claims=claims,
        company_name=company_name,
        report_id=report_id,
        api_key=api_key,
    )


def render_pdf_html(report_json: dict[str, Any], company_name: str) -> str:
    """A separate, deliberately short template for the PDF export — the web
    report's full per-company/BMC/VRIO/positioning detail doesn't fit on 1-2
    pages and isn't what a PDF export is for; this is the synthesis
    highlights only (Five Forces, SWOT, top recommendations), no citations."""
    template = _env.get_template("pdf_report.html")
    meta = report_json.get("meta", {})
    return template.render(
        company_name=company_name,
        tier=meta.get("tier", ""),
        data_as_of=meta.get("data_as_of", ""),
        forces=report_json.get("five_forces", {}).get("forces", []),
        swot=report_json.get("swot", {}),
        recommendations=report_json.get("recommendations", []),
    )


def render_home_html() -> str:
    """The stage 9 landing page — the browser-only entry point into
    /companies/resolve + /reports (previously API-only, curl required)."""
    return _env.get_template("home.html").render()


def render_progress_html(report_id: uuid.UUID, api_key: str | None, company_name: str) -> str:
    key_param = f"?api_key={api_key}" if api_key else ""
    return _env.get_template("progress.html").render(
        report_id=str(report_id), key_param=key_param, company_name=company_name
    )


def render_failed_html(report_id: uuid.UUID, api_key: str | None) -> str:
    return _env.get_template("failed.html").render(report_id=str(report_id), api_key=api_key or "")
