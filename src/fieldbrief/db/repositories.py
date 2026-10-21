"""Repository functions over the 8 tables (design.md §4).

Thin wrappers, not a full repository-pattern abstraction (CLAUDE.md: no
speculative abstractions) — just the handful of operations stage 3's graph
needs: dedup companies/sources, append claims, create/save a report.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.db.models import Claim, Company, Report, ReportClaim, Source, User

CLI_LOCAL_USER_EMAIL = "cli-local@fieldbrief.local"


def _utcnow() -> datetime:
    """Naive UTC, matching the DB columns (TIMESTAMP WITHOUT TIME ZONE)."""
    return datetime.now(UTC).replace(tzinfo=None)


async def get_or_create_cli_user(session: AsyncSession) -> User:
    """The CLI has no auth yet (that's stage 6) — one fixed local user owns
    every report run from the command line."""
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
    """Dedup by domain (unique) when we have one; otherwise this can't safely
    dedup and just creates a new row — acceptable for MVP (most resolvable
    companies have a domain; the rare case without one is a known gap, not
    silently wrong)."""
    if domain is not None:
        result = await session.execute(select(Company).where(Company.domain == domain))
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

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
    """Dedup by URL (unique). Returns (source, content_changed) so callers can
    skip re-extraction when the hash hasn't moved (design.md §4 invariant)."""
    result = await session.execute(select(Source).where(Source.url == url))
    existing = result.scalar_one_or_none()
    if existing is not None:
        changed = existing.content_hash != content_hash
        if changed:
            existing.content_hash = content_hash
            existing.fetched_at = _utcnow()
            await session.flush()
        return existing, changed

    source = Source(
        url=url,
        title=title,
        source_type=source_type,
        published_at=published_at,
        content_hash=content_hash,
    )
    session.add(source)
    await session.flush()
    return source, True


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


async def get_fresh_claims(
    session: AsyncSession, company_id: uuid.UUID, field: str, max_age: timedelta
) -> list[Claim]:
    """The cache lookup (design.md §4's hottest query) — fresh claims for
    (company, field), used by the planner to decide what's still a gap."""
    cutoff = _utcnow() - max_age
    result = await session.execute(
        select(Claim)
        .where(Claim.company_id == company_id, Claim.field == field, Claim.extracted_at >= cutoff)
        .order_by(Claim.extracted_at.desc())
    )
    return list(result.scalars().all())


async def create_report(
    session: AsyncSession, user_id: uuid.UUID, target_company_id: uuid.UUID, tier: str
) -> Report:
    report = Report(
        user_id=user_id, target_company_id=target_company_id, tier=tier, status="running"
    )
    session.add(report)
    await session.flush()
    return report


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
