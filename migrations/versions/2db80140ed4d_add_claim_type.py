"""add claim type

Revision ID: 2db80140ed4d
Revises: d37ccfd2f84a
Create Date: 2026-10-23 13:46:48.449570

ADR 0016: lets synthesis tell a reported fact apart from an attributed
claim, an analytical interpretation, or a hypothesis, so a finding resting
only on interpretation/hypothesis can be flagged instead of presented with
the same weight as a verified fact. Nullable: claims extracted before this
migration have no classification, which is a legitimate "unknown", not an
error.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "2db80140ed4d"
down_revision: str | Sequence[str] | None = "d37ccfd2f84a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    claim_type_enum = postgresql.ENUM(
        "reported_fact",
        "attributed_claim",
        "analytical_interpretation",
        "hypothesis",
        name="claim_type",
    )
    claim_type_enum.create(op.get_bind(), checkfirst=True)
    op.add_column("claims", sa.Column("claim_type", claim_type_enum, nullable=True))


def downgrade() -> None:
    op.drop_column("claims", "claim_type")
    postgresql.ENUM(name="claim_type").drop(op.get_bind(), checkfirst=True)
