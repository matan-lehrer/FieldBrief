"""add report audience

Revision ID: d37ccfd2f84a
Revises: e950c44c9078
Create Date: 2026-10-22 18:57:23.804443

Stage 4 of the reliability/correctness/UX overhaul (post-stage-9): who the
executive brief and audience-implications section are written for.
Persisted on the Report row (not just a transient job-message field, same
reasoning as competitor_count historically wasn't) so a refresh can read it
back. Existing rows default to "analyst".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d37ccfd2f84a"
down_revision: str | Sequence[str] | None = "e950c44c9078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    audience_enum = postgresql.ENUM(
        "analyst",
        "investor",
        "leadership",
        "competitor",
        "sales_partnership",
        name="report_audience",
    )
    # add_column on an existing table doesn't auto-create the ENUM type the
    # way create_table does (that one's own quirk) — create it explicitly.
    audience_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "reports",
        sa.Column("audience", audience_enum, server_default="analyst", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("reports", "audience")
    postgresql.ENUM(name="report_audience").drop(op.get_bind(), checkfirst=True)
