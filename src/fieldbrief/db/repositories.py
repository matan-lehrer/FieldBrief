"""Repository functions over the DB tables: dedup companies/sources,
append claims, create/save a report."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.consts import DEFAULT_FRESHNESS_MAX_AGE
from fieldbrief.db.models import (
    Claim,
    Company,
    CompanyCompetitor,
    Report,
    ReportClaim,
    ReportCompetitor,
    Source,
    User,
)

CLI_LOCAL_USER_EMAIL = "cli-local@fieldbrief.local"


def _utcnow() -> datetime:
    """Naive UTC, matching the DB columns (TIMESTAMP WITHOUT TIME ZONE)."""
    return datetime.now(UTC).replace(tzinfo=None)


async def get_or_create_cli_user(session: AsyncSession) -> User:
    """The CLI has no auth — one fixed local user owns every CLI run."""
    result = await session.execute(select(User).where(User.email == CLI_LOCAL_USER_EMAIL))
    user = result.scalar_one_or_none()
    if user is not None:
        return user
    user = User(email=CLI_LOCAL_USER_EMAIL)
    session.add(user)
    await session.flush()
    return user


async def get_or_create_company(
    session: AsyncSession,
    name: str,
    domain: str | None = None,
    aliases: list[str] | None = None,
    ticker: str | None = None,
    cik: str | None = None,
    industry: str | None = None,
) -> Company:
    """Dedup by domain via an ON CONFLICT upsert, not check-then-insert —
    concurrent report runs can race to create the same company. Domain-less
    companies can't be dedup'd this way (NULL never conflicts with NULL)."""
    if domain is not None:
        insert_stmt = (
            pg_insert(Company)
            .values(
                id=uuid.uuid4(),
                name=name,
                domain=domain,
                aliases=aliases or [],
                ticker=ticker,
                cik=cik,
                industry=industry,
            )
            .on_conflict_do_nothing(index_elements=["domain"])
            .returning(Company.id)
        )
        inserted_id = (await session.execute(insert_stmt)).scalar_one_or_none()
        if inserted_id is not None:
            return await session.get_one(Company, inserted_id)

        result = await session.execute(select(Company).where(Company.domain == domain))
        return result.scalar_one()

    company = Company(
        name=name,
        domain=domain,
        aliases=aliases or [],
        ticker=ticker,
        cik=cik,
        industry=industry,
    )
    session.add(company)
    await session.flush()
    return company


async def get_or_create_source(
    session: AsyncSession,
    url: str,
    content_hash: str,
    title: str | None = None,
    source_type: str = "official",
    published_at: datetime | None = None,
) -> tuple[Source, bool]:
    """Dedup by URL via an ON CONFLICT upsert, not check-then-insert — two
    concurrently researched companies can legitimately fetch the same URL.
    Returns (source, content_changed) so callers can skip re-extraction when
    the hash hasn't moved."""
    insert_stmt = (
        pg_insert(Source)
        .values(
            id=uuid.uuid4(),
            url=url,
            title=title,
            source_type=source_type,
            published_at=published_at,
            content_hash=content_hash,
        )
        .on_conflict_do_nothing(index_elements=["url"])
        .returning(Source.id)
    )
    inserted_id = (await session.execute(insert_stmt)).scalar_one_or_none()

    if inserted_id is not None:
        source = await session.get_one(Source, inserted_id)
        return source, True

    result = await session.execute(select(Source).where(Source.url == url))
    source = result.scalar_one()
    changed = source.content_hash != content_hash
    if changed:
        source.content_hash = content_hash
        source.fetched_at = _utcnow()
    await session.flush()
    return source, changed


async def add_claim(
    session: AsyncSession,
    company_id: uuid.UUID,
    source_id: uuid.UUID,
    field: str,
    statement: str,
    value: dict[str, Any],
    snippet: str,
    relevance: float,
    verified: bool = False,
) -> Claim:
    """Append-only (design.md §4 invariant) — never updates an existing claim."""
    claim = Claim(
        company_id=company_id,
        source_id=source_id,
        field=field,
        statement=statement,
        value=value,
        snippet=snippet,
        relevance=relevance,
        verified=verified,
    )
    session.add(claim)
    await session.flush()
    return claim


async def get_covered_fields_from_source(
    session: AsyncSession,
    company_id: uuid.UUID,
    source_id: uuid.UUID,
    fields: list[str],
    max_age_by_field: dict[str, timedelta],
) -> set[str]:
    """Which of `fields` already have a fresh claim for this exact (company,
    source) pair — used instead of the Source's global content_hash flag,
    which can't tell whether *this* company already extracted a field."""
    if not fields:
        return set()
    result = await session.execute(
        select(Claim.field, Claim.extracted_at).where(
            Claim.company_id == company_id,
            Claim.source_id == source_id,
            Claim.field.in_(fields),
        )
    )
    now = _utcnow()
    covered: set[str] = set()
    for field, extracted_at in result.all():
        max_age = max_age_by_field.get(field, DEFAULT_FRESHNESS_MAX_AGE)
        if extracted_at >= now - max_age:
            covered.add(field)
    return covered


async def get_fresh_claims(
    session: AsyncSession, company_id: uuid.UUID, field: str, max_age: timedelta
) -> list[Claim]:
    """Fresh claims for (company, field), used by the planner to decide
    what's still a gap."""
    cutoff = _utcnow() - max_age
    result = await session.execute(
        select(Claim)
        .where(Claim.company_id == company_id, Claim.field == field, Claim.extracted_at >= cutoff)
        .order_by(Claim.extracted_at.desc())
    )
    return list(result.scalars().all())


async def create_report(
    session: AsyncSession,
    user_id: uuid.UUID,
    target_company_id: uuid.UUID,
    tier: str,
    status: str = "running",
    idempotency_key: str | None = None,
    parent_report_id: uuid.UUID | None = None,
    audience: str = "analyst",
) -> Report:
    """`status` defaults to "running" for the CLI's synchronous run; the API
    passes status="queued" since a worker hasn't picked the job up yet.
    `parent_report_id` links a refresh to the report it refreshed — reports
    are immutable, so a refresh creates a new version rather than mutating
    the old one."""
    report = Report(
        user_id=user_id,
        target_company_id=target_company_id,
        tier=tier,
        status=status,
        idempotency_key=idempotency_key,
        parent_report_id=parent_report_id,
        audience=audience,
    )
    session.add(report)
    await session.flush()
    return report


async def get_report_by_idempotency_key(
    session: AsyncSession, user_id: uuid.UUID, idempotency_key: str
) -> Report | None:
    result = await session.execute(
        select(Report).where(Report.user_id == user_id, Report.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


async def get_report(session: AsyncSession, report_id: uuid.UUID) -> Report | None:
    return await session.get(Report, report_id)


async def list_reports_for_user(
    session: AsyncSession, user_id: uuid.UUID, limit: int = 50
) -> list[Report]:
    """Newest first, via the ix_reports_user_created index."""
    result = await session.execute(
        select(Report)
        .where(Report.user_id == user_id)
        .order_by(Report.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def save_report(
    session: AsyncSession,
    report_id: uuid.UUID,
    report_json: dict[str, Any],
    status: str,
    cost_usd: float,
    claim_ids: list[uuid.UUID],
) -> None:
    result = await session.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one()
    report.report_json = report_json
    report.status = status
    report.cost_usd = cost_usd
    report.completed_at = _utcnow()

    for claim_id in set(claim_ids):
        session.add(ReportClaim(report_id=report_id, claim_id=claim_id))
    await session.flush()


async def add_report_competitors(
    session: AsyncSession, report_id: uuid.UUID, competitor_ids: list[uuid.UUID]
) -> None:
    for competitor_id in set(competitor_ids):
        session.add(ReportCompetitor(report_id=report_id, company_id=competitor_id))
    await session.flush()


async def count_report_competitors(session: AsyncSession, report_id: uuid.UUID) -> int:
    """Competitor count isn't persisted on the Report row itself; a refresh
    reads it back from ReportCompetitor instead."""
    result = await session.execute(
        select(ReportCompetitor).where(ReportCompetitor.report_id == report_id)
    )
    return len(list(result.scalars().all()))


async def upsert_competitor_relationship(
    session: AsyncSession,
    company_id: uuid.UUID,
    competitor_id: uuid.UUID,
    relevance: float,
    origin: str = "discovered",
) -> None:
    """The competitor graph, reused across reports. Upserts so
    re-discovering the same pair refreshes relevance/updated_at rather than
    duplicating the row."""
    result = await session.execute(
        select(CompanyCompetitor).where(
            CompanyCompetitor.company_id == company_id,
            CompanyCompetitor.competitor_id == competitor_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.relevance = relevance
        existing.updated_at = _utcnow()
    else:
        session.add(
            CompanyCompetitor(
                company_id=company_id,
                competitor_id=competitor_id,
                relevance=relevance,
                origin=origin,
            )
        )
    await session.flush()
