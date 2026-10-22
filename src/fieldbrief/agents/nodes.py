"""Per-company research nodes (ADR 0010's inner, per-field fan-out level).
Each node is `async def node(state, **deps) -> dict` — a partial state update,
per LangGraph convention. Dependencies (DB session, LLM gateway, budget,
fetcher, HTTP client) are injected by agents/graph.py via functools.partial,
not carried in state (state is data, per ADR 0001).

analyze_node builds CompanySnapshot deterministically (see its docstring);
everything cross-company (Five Forces, BMC, VRIO, positioning, SWOT,
recommendations) lives in agents/synthesis.py, run once after every company's
analyze_node completes (agents/orchestrator.py).
"""

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
DEFAULT_FRESHNESS_MAX_AGE = timedelta(days=30)

# Fields with no dedicated search query share the overview/domain fetch
# (field-checklist.md: most Quick-tier fields and offering.value_prop are
# typically covered by a company's own "about" page). Everything else gets
# its own targeted search when it's still a gap after the overview.
SEARCH_GROUPS: list[tuple[set[str], str]] = [
    ({"events.recent"}, "{name} news"),
    ({"pricing.model"}, "{name} pricing"),
    ({"financials.profitability", "financials.funding_or_cap"}, "{name} funding financials"),
    # Split, not shared: VRIO (and therefore SWOT's Strengths/Weaknesses,
    # ADR 0009) depends entirely on resources.advantages claims. Sharing one
    # page with market.forces meant a page that covered industry dynamics
    # but never named a resource left VRIO (and S/W) empty on real runs.
    ({"resources.advantages"}, "{name} competitive advantage moat"),
    ({"market.forces"}, "{name} industry competition analysis"),
    ({"environment.regulation"}, "{name} regulation compliance"),
]
RESULTS_PER_SEARCH = 4  # enough headroom for a few retry rounds to each see a fresh result

# Fields official SEC filings can answer directly (10-K/20-F/40-F) — tried
# before the generic search path, and persisted as source_type="filing"
# (confidence.py's highest reliability tier) instead of scraped news/blog text.
EDGAR_FIELDS: set[str] = {
    "financials.revenue",
    "financials.profitability",
    "financials.funding_or_cap",
}

# Fields where a single source isn't enough to build real confidence on —
# these feed "predictions" directly, so fetch a second independent result in
# the same round instead of hoping a retry round happens to pick a new one
# (it usually doesn't: a retry round only fires if the gap is still open,
# and one successful source closes it).
CORROBORATE_FIELDS: set[str] = EDGAR_FIELDS | {"market.forces"}

# The two ADR-0007 tagged, multi-dimension fields: closing them on a single
# claim meant one claim about any one force/resource looked like "done,"
# even though the framework it feeds (Five Forces / VRIO, and therefore
# SWOT's Strengths/Weaknesses) needs several distinct sub-dimensions covered
# to be more than a guess.
TAGGED_FIELD_KEY: dict[str, str] = {
    "market.forces": "force",
    "resources.advantages": "resource",
}
MIN_DISTINCT_TAGS: dict[str, int] = {
    "market.forces": 3,  # of 5 possible forces
    "resources.advantages": 2,
}

# A handful of domain patterns worth classifying more precisely than the
# official-domain/everything-else split used to manage: a government filing
# or a company's own press release fetched via search shouldn't read as
# generic "news," and a personal blog platform shouldn't read as official.
_PRESS_WIRE_DOMAINS = {"prnewswire.com", "businesswire.com", "globenewswire.com"}
_BLOG_DOMAIN_HINTS = ("medium.com", "substack.com", "blogspot.com", "wordpress.com", "tumblr.com")

_NULL_PROGRESS = NullProgressSink()


def _max_age(field: str) -> timedelta:
    return FRESHNESS_MAX_AGE.get(field, DEFAULT_FRESHNESS_MAX_AGE)


def _normalize_for_match(text: str) -> str:
    return " ".join(text.split()).lower()


def _as_datetime(d: date | None) -> datetime | None:
    """Source.published_at is a naive-UTC datetime column (matching every
    other timestamp in db/repositories.py); metadata extraction only ever
    gives us a date, so midnight UTC on that date is as precise as it gets."""
    return datetime(d.year, d.month, d.day) if d else None


def _distinct_tags(claims: list[Claim], tag_key: str) -> set[str]:
    return {str(c.value[tag_key]) for c in claims if c.value.get(tag_key)}


def _field_resolved(fresh_claims: list[Claim], field: str) -> bool:
    """A field is "done" once it has at least one fresh, verified claim —
    and, for the two tagged multi-dimension fields, enough distinct
    sub-dimensions covered that the framework it feeds isn't built on a
    single guess."""
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
        return "official"  # a company-issued press release, not independent commentary
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
    """Shared tail end of "fetched a page -> extract claims -> verify each"
    (design.md §9) — used for both the normal search-result fetch loop and
    the EDGAR filing path below, which differ only in how the page/source
    was obtained."""
    extraction = await gateway.call(
        Task.EXTRACT,
        prompts.extraction_prompt(company_name, cleaned, fields),
        ExtractionResult,
        budget,
        report_id=report_id,
        stage="research",
    )
    for extracted in extraction.claims:
        # The model's own excerpt, not the whole page: a claim used to store
        # the entire cleaned page as its "snippet," so every claim from one
        # page duplicated the full article and nothing pointed at the actual
        # supporting sentence. Only trust the excerpt if it's genuinely
        # verbatim (a cheap, deterministic substring check) — a quote that
        # isn't actually in the page is a fabricated "source quotation," not
        # evidence, so it's dropped rather than displayed as one.
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
        # Verification still checks the statement against the full cleaned
        # page, not the (possibly empty/short) stored excerpt — a claim can
        # be genuinely supported by the page even when no single sentence
        # was a clean enough verbatim quote to keep.
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
    """Official-source financials (design.md hard constraint 5: accuracy over
    polish) — tried before the scraped-page path for companies with a CIK.
    Never raises: a 403/rate-limit from SEC (a known failure mode per
    fetch/edgar.py's own docstring) just means the normal search path below
    still runs, so EDGAR being unavailable never blocks the pipeline."""
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
    """One research round. The overview/domain page covers most fields in one
    extraction call (design.md §9: "fetch -> extract claims ... for all known
    companies on the page" — not a per-field fetch loop); fields that need
    their own angle (pricing, funding, regulation, ...) get one targeted
    search each, only when they're still a gap. Each retry round picks a
    different search result (round N -> results[N]) so looping again actually
    sees new sources instead of re-fetching (and skipping, via content_hash
    dedup) the same page."""
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

    # (url, search-result title or None — the domain-only overview fetch has
    # no search result to carry a title from; extract_metadata() below fills
    # that gap from the page itself when a search title isn't available).
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
            # A second, independent source in the SAME round — corroboration
            # otherwise never happens in practice: a retry round only fires
            # while the gap is still open, and one successful source closes
            # it immediately (reports/confidence.py's corroboration bonus
            # was dead code without this).
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
            # The page hasn't moved since someone last fetched it — but
            # "someone" might have been a different company, or a round
            # that didn't need today's gap fields. Only skip re-extraction
            # when THIS company already has fresh claims from THIS source
            # for every field this fetch is targeting.
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
    for gap in state["gaps"]:
        fresh = await get_fresh_claims(session, company_id, gap["field"], _max_age(gap["field"]))
        if _field_resolved(fresh, gap["field"]):
            continue  # resolved — drop from gaps
        remaining.append({"field": gap["field"], "attempts": gap["attempts"] + 1})
    return {"gaps": remaining, "retry_round": state["retry_round"] + 1}


def should_continue_researching(state: GraphState, *, tier: Tier) -> str:
    max_rounds = TIER_MAX_RETRY_ROUNDS[tier]
    if state["gaps"] and state["retry_round"] < max_rounds:
        return "loop"
    return "advance"


async def analyze_node(state: GraphState, *, session: AsyncSession) -> dict[str, Any]:
    """Deterministic CompanySnapshot assembly — grouping already-extracted,
    already-tagged claims by field is data wrangling, not judgment, so no LLM
    call here (cheaper, and nothing to hallucinate). The real judgment calls
    (Five Forces, VRIO, positioning, SWOT, recommendations) happen once,
    across all companies, in agents/synthesis.py after every company's
    analyze_node has run."""
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

    # Every verified claim ever extracted used to feed the snapshot, with no
    # age limit — a revenue claim from a year-old article and one from
    # yesterday blended into the same Citation as if they were the same
    # fact. Group by field for the "stale" check, but only fresh claims
    # (same per-field window gap_check already uses) ever reach a Citation.
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

    # Plain facts, not judgment calls — shown without a citation wrapper
    # (report.html already expects c.hq/c.founded as bare strings). Was
    # extracted and verified like any other claim but never actually reached
    # the snapshot: profile.hq_founded's claims existed in the DB with
    # nowhere to go. First verified claim that supplies each half wins.
    hq: str | None = None
    founded: str | None = None
    for claim in fresh_by_field.get("profile.hq_founded", []):
        if hq is None and claim.value.get("hq"):
            hq = str(claim.value["hq"])
        if founded is None and claim.value.get("founded_year"):
            founded = str(claim.value["founded_year"])

    # Official Wikidata facts already sitting on the Company row — free,
    # no extraction needed, and more reliable than anything scraped.
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
