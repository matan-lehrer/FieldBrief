"""SQLAlchemy models for the 8 tables in docs/design.md §4.

Source of truth is docs/design.md — update both together.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

CompetitorOrigin = PgEnum("discovered", "user", name="competitor_origin")
SourceType = PgEnum("filing", "official", "news", "blog", name="source_type")
ReportTier = PgEnum("quick", "standard", "deep", name="report_tier")
ReportStatus = PgEnum("queued", "running", "done", "partial", "failed", name="report_status")


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str]
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    domain: Mapped[str | None] = mapped_column(unique=True)
    ticker: Mapped[str | None]
    cik: Mapped[str | None]
    industry: Mapped[str | None]


class CompanyCompetitor(Base):
    __tablename__ = "company_competitors"

    company_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("companies.id"), primary_key=True
    )
    competitor_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("companies.id"), primary_key=True
    )
    relevance: Mapped[float]
    origin: Mapped[str] = mapped_column(CompetitorOrigin)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = _uuid_pk()
    url: Mapped[str] = mapped_column(unique=True)
    title: Mapped[str | None]
    source_type: Mapped[str] = mapped_column(SourceType)
    published_at: Mapped[datetime | None]
    fetched_at: Mapped[datetime] = mapped_column(server_default=func.now())
    content_hash: Mapped[str]


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("companies.id"))
    source_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("sources.id"))
    field: Mapped[str]
    statement: Mapped[str]
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    snippet: Mapped[str]
    relevance: Mapped[float]
    verified: Mapped[bool] = mapped_column(default=False)
    extracted_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # The cache lookup, hottest query (design.md §4): fresh claims for (company, field).
    __table_args__ = (
        Index("ix_claims_company_field_extracted", "company_id", "field", "extracted_at"),
    )


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    target_company_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("companies.id")
    )
    parent_report_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("reports.id")
    )
    tier: Mapped[str] = mapped_column(ReportTier)
    status: Mapped[str] = mapped_column(ReportStatus, default="queued")
    report_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cost_usd: Mapped[float | None] = mapped_column(Numeric)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None]

    __table_args__ = (
        Index("ix_reports_user_created", "user_id", "created_at"),
        Index("ix_reports_target_created", "target_company_id", "created_at"),
    )


class ReportCompetitor(Base):
    __tablename__ = "report_competitors"

    report_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("reports.id"), primary_key=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("companies.id"), primary_key=True
    )


class ReportClaim(Base):
    __tablename__ = "report_claims"

    report_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("reports.id"), primary_key=True
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("claims.id"), primary_key=True
    )


__all__ = [
    "Base",
    "Claim",
    "Company",
    "CompanyCompetitor",
    "Report",
    "ReportClaim",
    "ReportCompetitor",
    "Source",
    "User",
]
