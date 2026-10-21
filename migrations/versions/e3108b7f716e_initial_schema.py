"""initial schema

Revision ID: e3108b7f716e
Revises:
Create Date: 2026-10-21 16:27:56.948797

The 8 tables from docs/design.md §4, matching src/fieldbrief/db/models.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e3108b7f716e"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("domain", sa.String(), unique=True),
        sa.Column("ticker", sa.String()),
        sa.Column("cik", sa.String()),
        sa.Column("industry", sa.String()),
    )

    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("url", sa.String(), nullable=False, unique=True),
        sa.Column("title", sa.String()),
        sa.Column(
            "source_type",
            postgresql.ENUM("filing", "official", "news", "blog", name="source_type"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime()),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
    )

    op.create_table(
        "company_competitors",
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id"),
            primary_key=True,
        ),
        sa.Column(
            "competitor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id"),
            primary_key=True,
        ),
        sa.Column("relevance", sa.Float(), nullable=False),
        sa.Column(
            "origin",
            postgresql.ENUM("discovered", "user", name="competitor_origin"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id"),
            nullable=False,
        ),
        sa.Column(
            "source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False
        ),
        sa.Column("field", sa.String(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("relevance", sa.Float(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("extracted_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    # The cache lookup, hottest query (design.md §4): fresh claims for (company, field).
    op.create_index(
        "ix_claims_company_field_extracted", "claims", ["company_id", "field", "extracted_at"]
    )

    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "target_company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id"),
            nullable=False,
        ),
        sa.Column("parent_report_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("reports.id")),
        sa.Column(
            "tier", postgresql.ENUM("quick", "standard", "deep", name="report_tier"), nullable=False
        ),
        sa.Column(
            "status",
            postgresql.ENUM("queued", "running", "done", "partial", "failed", name="report_status"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("report_json", postgresql.JSONB()),
        sa.Column("cost_usd", sa.Numeric()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
    )
    op.create_index("ix_reports_user_created", "reports", ["user_id", "created_at"])
    op.create_index("ix_reports_target_created", "reports", ["target_company_id", "created_at"])

    op.create_table(
        "report_competitors",
        sa.Column(
            "report_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reports.id"),
            primary_key=True,
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id"),
            primary_key=True,
        ),
    )

    op.create_table(
        "report_claims",
        sa.Column(
            "report_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reports.id"),
            primary_key=True,
        ),
        sa.Column(
            "claim_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("claims.id"), primary_key=True
        ),
    )


def downgrade() -> None:
    op.drop_table("report_claims")
    op.drop_table("report_competitors")
    op.drop_index("ix_reports_target_created", table_name="reports")
    op.drop_index("ix_reports_user_created", table_name="reports")
    op.drop_table("reports")
    op.drop_index("ix_claims_company_field_extracted", table_name="claims")
    op.drop_table("claims")
    op.drop_table("company_competitors")
    op.drop_table("sources")
    op.drop_table("companies")
    op.drop_table("users")

    postgresql.ENUM(name="report_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="report_tier").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="competitor_origin").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="source_type").drop(op.get_bind(), checkfirst=True)
