"""Repository tests against a real local Postgres (docker compose up -d postgres).

Not marked `live` — that marker is for external network APIs/LLMs (CLAUDE.md);
this is local infrastructure the compose stack already provides. Every test
runs inside one uncommitted transaction, rolled back at teardown — the
repository functions only ever flush(), never commit(), so nothing persists.
"""

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.db.repositories import (
    add_claim,
    create_report,
    get_covered_fields_from_source,
    get_fresh_claims,
    get_or_create_cli_user,
    get_or_create_company,
    get_or_create_source,
    save_report,
)
from fieldbrief.db.session import SessionFactory, engine


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """Each test gets its own event loop (pytest-asyncio default); asyncpg
    connections are loop-bound, so the pool must be disposed after every test
    or the next test's loop tries to reuse a connection from a closed one."""
    async with SessionFactory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_or_create_company_dedups_by_domain(session: AsyncSession) -> None:
    domain = f"https://wix-{uuid.uuid4().hex[:8]}.example.com/"
    first = await get_or_create_company(session, name="Wix.com", domain=domain)
    second = await get_or_create_company(session, name="Wix.com (dup)", domain=domain)

    assert first.id == second.id
    assert second.name == "Wix.com"  # the original row, not overwritten by the dup call


@pytest.mark.asyncio
async def test_get_or_create_source_reports_content_change(session: AsyncSession) -> None:
    url = f"https://wix-{uuid.uuid4().hex[:8]}.example.com/about"
    source, changed1 = await get_or_create_source(session, url=url, content_hash="hash-a")
    assert changed1 is True

    same, changed2 = await get_or_create_source(session, url=url, content_hash="hash-a")
    assert same.id == source.id
    assert changed2 is False

    updated, changed3 = await get_or_create_source(session, url=url, content_hash="hash-b")
    assert updated.id == source.id
    assert changed3 is True
    assert updated.content_hash == "hash-b"


@pytest.mark.asyncio
async def test_get_or_create_source_stores_and_updates_content_fingerprint(
    session: AsyncSession,
) -> None:
    url = f"https://wix-{uuid.uuid4().hex[:8]}.example.com/about"
    source, _ = await get_or_create_source(
        session, url=url, content_hash="hash-a", content_fingerprint="fp-a"
    )
    assert source.content_fingerprint == "fp-a"

    updated, _ = await get_or_create_source(
        session, url=url, content_hash="hash-b", content_fingerprint="fp-b"
    )
    assert updated.content_fingerprint == "fp-b"


@pytest.mark.asyncio
async def test_add_claim_and_get_fresh_claims(session: AsyncSession) -> None:
    domain = f"https://wix-{uuid.uuid4().hex[:8]}.example.com/"
    company = await get_or_create_company(session, name="Wix.com", domain=domain)
    source, _ = await get_or_create_source(session, url=domain, content_hash="h1")

    await add_claim(
        session,
        company_id=company.id,
        source_id=source.id,
        field="profile.description",
        statement="Wix is a website builder.",
        value={},
        snippet="Wix.com is a cloud-based web development platform.",
        relevance=0.9,
        verified=True,
    )

    fresh = await get_fresh_claims(
        session, company_id=company.id, field="profile.description", max_age=timedelta(days=30)
    )
    assert len(fresh) == 1
    assert fresh[0].statement == "Wix is a website builder."

    stale_only = await get_fresh_claims(
        session, company_id=company.id, field="profile.description", max_age=timedelta(seconds=-1)
    )
    assert stale_only == []


@pytest.mark.asyncio
async def test_get_covered_fields_from_source_is_scoped_per_company(
    session: AsyncSession,
) -> None:
    """The real dedup bug: a Source's content_hash is global, so "unchanged"
    used to mean "skip" for ANY company or field, not just the one that
    already has a claim from it. This must say a field is only "covered"
    when THIS company has a fresh claim from THIS source for it."""
    domain_a = f"https://company-a-{uuid.uuid4().hex[:8]}.example.com/"
    domain_b = f"https://company-b-{uuid.uuid4().hex[:8]}.example.com/"
    company_a = await get_or_create_company(session, name="Company A", domain=domain_a)
    company_b = await get_or_create_company(session, name="Company B", domain=domain_b)
    shared_url = f"https://industry-report-{uuid.uuid4().hex[:8]}.example.com/"
    source, _ = await get_or_create_source(session, url=shared_url, content_hash="h1")

    await add_claim(
        session,
        company_id=company_a.id,
        source_id=source.id,
        field="profile.description",
        statement="Company A makes widgets.",
        value={},
        snippet="",
        relevance=0.9,
        verified=True,
    )

    max_ages = {"profile.description": timedelta(days=30), "offering.products": timedelta(days=30)}

    # Company A already has a fresh claim for this field from this source.
    covered_a = await get_covered_fields_from_source(
        session, company_a.id, source.id, ["profile.description"], max_ages
    )
    assert covered_a == {"profile.description"}

    # Company B has never touched this source at all — nothing covered,
    # even though the Source row's content hasn't changed since A fetched it.
    covered_b = await get_covered_fields_from_source(
        session, company_b.id, source.id, ["profile.description"], max_ages
    )
    assert covered_b == set()

    # A new field for company A, from the same already-seen source, isn't
    # covered either — one claim from a page doesn't cover every field.
    covered_new_field = await get_covered_fields_from_source(
        session, company_a.id, source.id, ["offering.products"], max_ages
    )
    assert covered_new_field == set()


@pytest.mark.asyncio
async def test_claims_are_append_only(session: AsyncSession) -> None:
    """Calling add_claim twice for the same field creates two rows, never updates one."""
    domain = f"https://wix-{uuid.uuid4().hex[:8]}.example.com/"
    company = await get_or_create_company(session, name="Wix.com", domain=domain)
    source, _ = await get_or_create_source(session, url=domain, content_hash="h1")

    await add_claim(
        session,
        company_id=company.id,
        source_id=source.id,
        field="financials.revenue",
        statement="Revenue was $400M",
        value={},
        snippet="...",
        relevance=0.8,
    )
    await add_claim(
        session,
        company_id=company.id,
        source_id=source.id,
        field="financials.revenue",
        statement="Revenue was $412M (corrected)",
        value={},
        snippet="...",
        relevance=0.8,
    )

    all_claims = await get_fresh_claims(
        session, company_id=company.id, field="financials.revenue", max_age=timedelta(days=30)
    )
    assert len(all_claims) == 2


@pytest.mark.asyncio
async def test_create_and_save_report(session: AsyncSession) -> None:
    user = await get_or_create_cli_user(session)
    domain = f"https://wix-{uuid.uuid4().hex[:8]}.example.com/"
    company = await get_or_create_company(session, name="Wix.com", domain=domain)
    source, _ = await get_or_create_source(session, url=domain, content_hash="h1")
    claim = await add_claim(
        session,
        company_id=company.id,
        source_id=source.id,
        field="profile.description",
        statement="x",
        value={},
        snippet="x",
        relevance=0.9,
    )

    report = await create_report(
        session, user_id=user.id, target_company_id=company.id, tier="quick"
    )
    assert report.status == "running"

    await save_report(
        session,
        report_id=report.id,
        report_json={"meta": {"tier": "quick"}},
        status="done",
        cost_usd=0.01,
        claim_ids=[claim.id],
    )

    await session.refresh(report)
    assert report.status == "done"
    assert report.report_json == {"meta": {"tier": "quick"}}
    assert report.cost_usd is not None


@pytest.mark.asyncio
async def test_create_report_defaults_audience_to_analyst(session: AsyncSession) -> None:
    user = await get_or_create_cli_user(session)
    domain = f"https://audience-test-{uuid.uuid4().hex[:8]}.example.com/"
    company = await get_or_create_company(session, name="Audience Test Co", domain=domain)

    default_report = await create_report(
        session, user_id=user.id, target_company_id=company.id, tier="quick"
    )
    assert default_report.audience == "analyst"

    investor_report = await create_report(
        session, user_id=user.id, target_company_id=company.id, tier="quick", audience="investor"
    )
    assert investor_report.audience == "investor"
