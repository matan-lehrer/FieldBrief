"""Per-company research nodes (ADR 0010's inner, per-field fan-out level).
Each node is `async def node(state, **deps) -> dict`, a partial state
update; dependencies are injected by agents/graph.py via functools.partial.

Cross-company judgment (Five Forces, BMC, VRIO, positioning, SWOT,
recommendations) lives in agents/synthesis.py, after every company's
analyze_node completes.
"""

import time
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.agents import prompts
from fieldbrief.agents.field_checklist import MAX_RETRY_ROUNDS as TIER_MAX_RETRY_ROUNDS
from fieldbrief.agents.field_checklist import Tier, fields_for
from fieldbrief.agents.llm_schemas import ClaimVerification, ExtractionResult
from fieldbrief.agents.progress import NullProgressSink, ProgressSink, Stage
from fieldbrief.agents.state import GraphState
from fieldbrief.consts import DEFAULT_FRESHNESS_MAX_AGE
from fieldbrief.db.models import Claim, Company, Source
from fieldbrief.db.repositories import (
    add_claim,
    get_covered_fields_from_source,
    get_fresh_claims,
    get_or_create_source,
)
from fieldbrief.fetch.clean import clean_html, content_hash, extract_metadata
from fieldbrief.fetch.edgar import get_recent_annual_filings
from fieldbrief.fetch.http import Fetcher
from fieldbrief.fetch.search import search
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway, Task
from fieldbrief.reports.confidence import compute_citation

MAX_ATTEMPTS_PER_FIELD = 2

FRESHNESS_MAX_AGE: dict[str, timedelta] = {
    "events.recent": timedelta(days=7),
    "financials.revenue": timedelta(days=365),
}

# Fields with no dedicated search query share the overview/domain fetch;
# everything else gets its own targeted search when still a gap.
SEARCH_GROUPS: list[tuple[set[str], str]] = [
    ({"events.recent"}, "{name} news"),
    ({"pricing.model"}, "{name} pricing"),
    ({"financials.profitability", "financials.funding_or_cap"}, "{name} funding financials"),
    ({"resources.advantages"}, "{name} competitive advantage moat"),
    ({"market.forces"}, "{name} industry competition analysis"),
    ({"environment.regulation"}, "{name} regulation compliance"),
]
RESULTS_PER_SEARCH = 4

# Fields official SEC filings answer directly — tried before the generic
# search path, persisted as source_type="filing" (confidence.py's top tier).
EDGAR_FIELDS: set[str] = {
    "financials.revenue",
    "financials.profitability",
    "financials.funding_or_cap",
}

# Fields that fetch a second independent source in the same round rather
# than relying on a retry round (one that only fires while the gap stays
# open, and closes the instant a single source succeeds).
CORROBORATE_FIELDS: set[str] = EDGAR_FIELDS | {"market.forces"}

# ADR 0007's two tagged, multi-dimension fields need several distinct
# sub-dimensions covered before the framework they feed is more than a guess.
TAGGED_FIELD_KEY: dict[str, str] = {
    "market.forces": "force",
    "resources.advantages": "resource",
}
MIN_DISTINCT_TAGS: dict[str, int] = {
    "market.forces": 3,  # of 5 possible forces
    "resources.advantages": 2,
}

_PRESS_WIRE_DOMAINS = {"prnewswire.com", "businesswire.com", "globenewswire.com"}
_BLOG_DOMAIN_HINTS = ("medium.com", "substack.com", "blogspot.com", "wordpress.com", "tumblr.com")

_NULL_PROGRESS = NullProgressSink()


def _max_age(field: str) -> timedelta:
    return FRESHNESS_MAX_AGE.get(field, DEFAULT_FRESHNESS_MAX_AGE)


def _normalize_for_match(text: str) -> str:
    return " ".join(text.split()).lower()


def _as_datetime(d: date | None) -> datetime | None:
    """Source.published_at is a naive-UTC datetime column; metadata
    extraction only ever gives a date, so midnight on that date it is."""
    return datetime(d.year, d.month, d.day) if d else None


def _distinct_tags(claims: list[Claim], tag_key: str) -> set[str]:
    return {str(c.value[tag_key]) for c in claims if c.value.get(tag_key)}


def _field_resolved(fresh_claims: list[Claim], field: str) -> bool:
    """A field is done once it has >=1 fresh verified claim, and — for the
    two tagged multi-dimension fields — enough distinct tags covered."""
    verified_fresh = [c for c in fresh_claims if c.verified]
    if not verified_fresh:
        return False
    tag_key = TAGGED_FIELD_KEY.get(field)
    if tag_key is None:
        return True
    return len(_distinct_tags(verified_fresh, tag_key)) >= MIN_DISTINCT_TAGS[field]


def _source_type(url: str, company_domain: str | None) -> str:
    netloc = urlparse(url).netloc.lower().removeprefix("www.")
    if company_domain:
        domain_netloc = urlparse(company_domain).netloc.lower().removeprefix("www.")
        if netloc == domain_netloc:
            return "official"
    if netloc.endswith(".gov"):
        return "filing"
    if netloc in _PRESS_WIRE_DOMAINS:
        return "official"
    if any(hint in netloc for hint in _BLOG_DOMAIN_HINTS):
        return "blog"
    return "news"


async def plan_node(state: GraphState, *, session: AsyncSession) -> dict[str, Any]:
    company_id = uuid.UUID(state["company_id"])
    fields = fields_for(state["tier"], state["is_target"])
    gaps = []
    for field in fields:
        fresh = await get_fresh_claims(session, company_id, field, _max_age(field))
        if not _field_resolved(fresh, field):
            gaps.append({"field": field, "attempts": 0})
    return {"gaps": gaps, "retry_round": 0}


async def _extract_and_store_claims(
    *,
    session: AsyncSession,
    gateway: LLMGateway,
    budget: Budget,
    report_id: str,
    company_id: uuid.UUID,
    company_name: str,
    source: Source,
    cleaned: str,
    fields: list[str],
    claim_ids: list[str],
) -> None:
    """Fetch -> extract claims -> verify each, shared by the normal
    search-result path and the EDGAR filing path below."""
    extraction = await gateway.call(
        Task.EXTRACT,
        prompts.extraction_prompt(company_name, cleaned, fields),
        ExtractionResult,
        budget,
        report_id=report_id,
        stage="research",
    )
    for extracted in extraction.claims:
        # Only keep the excerpt if it's genuinely verbatim in the page — a
        # quote that isn't actually there would be a fabricated "source
        # quotation," not evidence.
        excerpt = extracted.excerpt
        if excerpt and _normalize_for_match(excerpt) not in _normalize_for_match(cleaned):
            excerpt = None
        value = dict(extracted.value)
        if extracted.bmc_block:
            value["bmc_block"] = extracted.bmc_block
        claim = await add_claim(
            session,
            company_id=company_id,
            source_id=source.id,
            field=extracted.field,
            statement=extracted.statement,
            value=value,
            snippet=excerpt or "",
            relevance=extracted.relevance,
        )
        # Verification checks the statement against the full page, not the
        # (possibly empty) stored excerpt.
        verification = await gateway.call(
            Task.VERIFY,
            prompts.verification_prompt(extracted.statement, cleaned),
            ClaimVerification,
            budget,
            report_id=report_id,
            stage="verify",
        )
        claim.verified = verification.verified
        await session.flush()
        claim_ids.append(str(claim.id))


async def _research_financials_via_edgar(
    *,
    session: AsyncSession,
    gateway: LLMGateway,
    budget: Budget,
    edgar_client: httpx.AsyncClient,
    company_id: uuid.UUID,
    company_name: str,
    cik: str,
    report_id: str,
    fields: list[str],
    claim_ids: list[str],
) -> None:
    """Official-source financials, tried before the scraped-page path for
    companies with a CIK. Never raises — a 403/rate-limit from SEC just
    means the normal search path below still runs."""
    try:
        filings = await get_recent_annual_filings(edgar_client, cik, limit=1)
    except httpx.HTTPError:
        return
    for filing in filings:
        try:
            response = await edgar_client.get(filing.document_url)
            response.raise_for_status()
        except httpx.HTTPError:
            continue
        cleaned = clean_html(response.text)
        if cleaned is None:
            continue
        title, published_date = extract_metadata(response.text)
        source, changed = await get_or_create_source(
            session,
            url=filing.document_url,
            content_hash=content_hash(cleaned),
            source_type="filing",
            title=title,
            published_at=_as_datetime(published_date),
        )
        if not changed:
            covered = await get_covered_fields_from_source(
                session, company_id, source.id, fields, {f: _max_age(f) for f in fields}
            )
            if covered.issuperset(fields):
                continue
        await _extract_and_store_claims(
            session=session,
            gateway=gateway,
            budget=budget,
            report_id=report_id,
            company_id=company_id,
            company_name=company_name,
            source=source,
            cleaned=cleaned,
            fields=fields,
            claim_ids=claim_ids,
        )


async def research_node(
    state: GraphState,
    *,
    session: AsyncSession,
    gateway: LLMGateway,
    budget: Budget,
    fetcher: Fetcher,
    http_client: httpx.AsyncClient,
    searxng_url: str,
    edgar_client: httpx.AsyncClient | None = None,
    progress: ProgressSink = _NULL_PROGRESS,
) -> dict[str, Any]:
    """One research round: the overview/domain page covers most fields in
    one extraction call; fields needing their own angle get a targeted
    search each. Each retry round picks a different search result so
    looping again sees new sources instead of re-fetching the same page."""
    if not state["gaps"]:
        return {}

    company_id = uuid.UUID(state["company_id"])
    company_name = state["company_name"]
    company_domain = state["company_domain"]
    gap_fields = {g["field"] for g in state["gaps"]}
    round_index = state["retry_round"]
    claim_ids = list(state["claim_ids"])

    await progress.emit(Stage.RESEARCHING, f"Researching {company_name}")

    cik = state.get("company_cik")
    edgar_fields = sorted(EDGAR_FIELDS & gap_fields)
    if cik and edgar_fields and edgar_client is not None:
        await progress.emit(Stage.RESEARCHING, f"{company_name}: checking SEC filings")
        await _research_financials_via_edgar(
            session=session,
            gateway=gateway,
            budget=budget,
            edgar_client=edgar_client,
            company_id=company_id,
            company_name=company_name,
            cik=cik,
            report_id=state["report_id"],
            fields=edgar_fields,
            claim_ids=claim_ids,
        )

    # (url, search-result title or None — extract_metadata() below fills
    # the title in from the page itself when there's no search title).
    urls: list[tuple[str, str | None]] = []
    if company_domain:
        urls.append((company_domain, None))
    else:
        results = await search(
            http_client, searxng_url, f"{company_name} company overview", limit=RESULTS_PER_SEARCH
        )
        if results:
            picked = results[min(round_index, len(results) - 1)]
            urls.append((picked.url, picked.title))

    for group_fields, query_template in SEARCH_GROUPS:
        if not (group_fields & gap_fields):
            continue
        results = await search(
            http_client,
            searxng_url,
            query_template.format(name=company_name),
            limit=RESULTS_PER_SEARCH,
        )
        if not results:
            continue
        picks = [results[min(round_index, len(results) - 1)]]
        if group_fields & CORROBORATE_FIELDS and len(results) > 1:
            second = results[min(round_index + 1, len(results) - 1)]
            if second.url not in {p.url for p in picks}:
                picks.append(second)
        urls.extend((p.url, p.title) for p in picks)

    for url, search_title in urls:
        await progress.emit(Stage.RESEARCHING, f"{company_name}: reading {urlparse(url).netloc}")
        html = await fetcher.fetch(url)
        if html is None:
            continue
        cleaned = clean_html(html)
        if cleaned is None:
            continue

        meta_title, published_date = extract_metadata(html)
        source, changed = await get_or_create_source(
            session,
            url=url,
            content_hash=content_hash(cleaned),
            source_type=_source_type(url, company_domain),
            title=search_title or meta_title,
            published_at=_as_datetime(published_date),
        )
        if not changed:
            # Only skip re-extraction when THIS company already has fresh
            # claims from THIS source for every field this fetch targets —
            # "unchanged" might mean a different company fetched it last.
            covered = await get_covered_fields_from_source(
                session,
                company_id,
                source.id,
                sorted(gap_fields),
                {f: _max_age(f) for f in gap_fields},
            )
            if covered.issuperset(gap_fields):
                continue

        await _extract_and_store_claims(
            session=session,
            gateway=gateway,
            budget=budget,
            report_id=state["report_id"],
            company_id=company_id,
            company_name=company_name,
            source=source,
            cleaned=cleaned,
            fields=sorted(gap_fields),
            claim_ids=claim_ids,
        )

    return {"claim_ids": claim_ids}


async def gap_check_node(state: GraphState, *, session: AsyncSession) -> dict[str, Any]:
    company_id = uuid.UUID(state["company_id"])
    remaining = []
    # Dropped from `gaps` once a field hits its attempt cap so it stops
    # being retried, but tracked in `exhausted_gaps` so it still surfaces
    # as a real CoverageGap instead of disappearing.
    exhausted = list(state.get("exhausted_gaps", []))
    for gap in state["gaps"]:
        fresh = await get_fresh_claims(session, company_id, gap["field"], _max_age(gap["field"]))
        if _field_resolved(fresh, gap["field"]):
            continue  # resolved — drop from gaps
        attempts = gap["attempts"] + 1
        if attempts >= MAX_ATTEMPTS_PER_FIELD:
            exhausted.append({"field": gap["field"], "attempts": attempts})
            continue
        remaining.append({"field": gap["field"], "attempts": attempts})
    return {
        "gaps": remaining,
        "exhausted_gaps": exhausted,
        "retry_round": state["retry_round"] + 1,
    }


def should_continue_researching(
    state: GraphState, *, tier: Tier, deadline: float | None = None
) -> str:
    if deadline is not None and time.monotonic() >= deadline:
        return "advance"
    max_rounds = TIER_MAX_RETRY_ROUNDS[tier]
    if state["gaps"] and state["retry_round"] < max_rounds:
        return "loop"
    return "advance"


async def analyze_node(state: GraphState, *, session: AsyncSession) -> dict[str, Any]:
    """Deterministic CompanySnapshot assembly — grouping already-tagged
    claims by field is data wrangling, not judgment, so no LLM call here."""
    company_id = uuid.UUID(state["company_id"])

    claims_result = await session.execute(
        select(Claim)
        .where(Claim.company_id == company_id, Claim.verified.is_(True))
        .order_by(Claim.extracted_at)
    )
    claims = list(claims_result.scalars().all())

    source_ids = {c.source_id for c in claims}
    sources: dict[uuid.UUID, Source] = {}
    if source_ids:
        sources_result = await session.execute(select(Source).where(Source.id.in_(source_ids)))
        sources = {s.id: s for s in sources_result.scalars().all()}

    # Group by field for the "stale" check, but only fresh claims (same
    # per-field window gap_check uses) ever reach a Citation.
    now = datetime.now(UTC).replace(tzinfo=None)
    by_field: dict[str, list[Claim]] = defaultdict(list)
    fresh_by_field: dict[str, list[Claim]] = defaultdict(list)
    for claim in claims:
        by_field[claim.field].append(claim)
        if claim.extracted_at >= now - _max_age(claim.field):
            fresh_by_field[claim.field].append(claim)
    stale = bool(set(by_field) - set(fresh_by_field))

    def citation_for(field: str) -> dict[str, Any] | None:
        field_claims = fresh_by_field.get(field)
        if not field_claims:
            return None
        return compute_citation(field_claims, sources).model_dump(mode="json")

    # Plain facts, not judgment calls — shown without a citation wrapper.
    # First verified claim that supplies each half wins.
    hq: str | None = None
    founded: str | None = None
    for claim in fresh_by_field.get("profile.hq_founded", []):
        if hq is None and claim.value.get("hq"):
            hq = str(claim.value["hq"])
        if founded is None and claim.value.get("founded_year"):
            founded = str(claim.value["founded_year"])

    # Wikidata facts already sitting on the Company row — no extraction needed.
    company = await session.get(Company, company_id)
    industry = company.industry if company else None
    ticker = company.ticker if company else None

    snapshot = {
        "company_id": str(company_id),
        "name": state["company_name"],
        "is_target": state["is_target"],
        "description": citation_for("profile.description"),
        "hq": hq,
        "founded": founded,
        "industry": industry,
        "ticker": ticker,
        "employees": citation_for("profile.employees"),
        "products": citation_for("offering.products"),
        "customer_segments": citation_for("customers.segments"),
        "revenue_model": citation_for("revenue.model"),
        "revenue": citation_for("financials.revenue"),
        "profitability": citation_for("financials.profitability"),
        "funding_or_cap": citation_for("financials.funding_or_cap"),
        "recent_events": citation_for("events.recent"),
        "freshness": {"as_of": date.today().isoformat(), "stale": stale},
    }
    return {"company_snapshot": snapshot}
