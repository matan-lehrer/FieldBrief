import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.db.models import Claim, Source

TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
    undefined=ChainableUndefined,
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
    for finding in report_json.get("executive_brief", []):
        ids.update(finding["claim_ids"])
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
    for row in report_json.get("comparison", {}).get("rows", []):
        for cell in row["cells"]:
            citation = cell.get("citation")
            if citation:
                ids.update(citation["claim_ids"])
    for rec in report_json.get("recommendations", []):
        ids.update(rec["claim_ids"])
    implications = report_json.get("audience_implications")
    if implications:
        ids.update(implications["claim_ids"])
    return ids


def _publisher_label(source: Source) -> str:
    if source.title:
        return source.title
    return urlparse(source.url).netloc.removeprefix("www.")


def _published_date(source: Source) -> str | None:
    return source.published_at.date().isoformat() if source.published_at else None


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
            "excerpt": c.snippet or None,
            "source_url": sources[c.source_id].url,
            "source_type": sources[c.source_id].source_type,
            "publisher": _publisher_label(sources[c.source_id]),
            "published_at": _published_date(sources[c.source_id]),
        }
        for c in claims
        if c.source_id in sources
    }


def assign_source_numbers(
    report_json: dict[str, Any], claims: dict[str, dict[str, Any]]
) -> dict[str, int]:
    claim_number: dict[str, int] = {}
    url_number: dict[str, int] = {}

    def visit(ids: list[str]) -> None:
        for cid in ids:
            claim = claims.get(cid)
            if claim is None:
                continue
            url = claim["source_url"]
            if url not in url_number:
                url_number[url] = len(url_number) + 1
            claim_number[cid] = url_number[url]

    for finding in report_json.get("executive_brief", []):
        visit(finding["claim_ids"])
    for company in report_json.get("companies", []):
        for key in _SNAPSHOT_CITATION_FIELDS:
            citation = company.get(key)
            if citation:
                visit(citation["claim_ids"])
    for force in report_json.get("five_forces", {}).get("forces", []):
        visit(force["claim_ids"])
    for block in report_json.get("bmc", {}).get("blocks", []):
        visit(block["claim_ids"])
    for resource in report_json.get("vrio", []):
        visit(resource["claim_ids"])
    for placed in report_json.get("positioning", {}).get("companies", []):
        visit(placed["claim_ids"])
    for row in report_json.get("comparison", {}).get("rows", []):
        for cell in row["cells"]:
            citation = cell.get("citation")
            if citation:
                visit(citation["claim_ids"])
    for items in report_json.get("swot", {}).values():
        for item in items:
            visit(item["claim_ids"])
    for rec in report_json.get("recommendations", []):
        visit(rec["claim_ids"])
    implications = report_json.get("audience_implications")
    if implications:
        visit(implications["claim_ids"])
    return claim_number


def numbered_sources(
    claims: dict[str, dict[str, Any]], claim_number: dict[str, int]
) -> list[dict[str, Any]]:
    by_number: dict[int, dict[str, Any]] = {}
    for cid, number in claim_number.items():
        if number not in by_number:
            claim = claims[cid]
            by_number[number] = {
                "number": number,
                "publisher": claim["publisher"],
                "published_at": claim["published_at"],
                "source_url": claim["source_url"],
            }
    return [by_number[n] for n in sorted(by_number)]


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


def render_pdf_html(
    report_json: dict[str, Any], claims: dict[str, dict[str, Any]], company_name: str
) -> str:
    template = _env.get_template("pdf_report.html")
    source_numbers = assign_source_numbers(report_json, claims)
    return template.render(
        report=report_json,
        claims=claims,
        company_name=company_name,
        source_numbers=source_numbers,
        sources_list=numbered_sources(claims, source_numbers),
    )


def render_home_html() -> str:
    return _env.get_template("home.html").render()


def render_progress_html(report_id: uuid.UUID, api_key: str | None, company_name: str) -> str:
    key_param = f"?api_key={api_key}" if api_key else ""
    return _env.get_template("progress.html").render(
        report_id=str(report_id), key_param=key_param, company_name=company_name
    )


def render_failed_html(report_id: uuid.UUID, api_key: str | None) -> str:
    return _env.get_template("failed.html").render(report_id=str(report_id), api_key=api_key or "")


def render_history_html(rows: list[dict[str, Any]], api_key: str | None) -> str:
    return _env.get_template("history.html").render(rows=rows, api_key=api_key or "")
