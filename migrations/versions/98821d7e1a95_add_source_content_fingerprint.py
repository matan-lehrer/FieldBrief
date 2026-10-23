"""add source content fingerprint

Revision ID: 98821d7e1a95
Revises: 2db80140ed4d
Create Date: 2026-10-23 14:30:00.000000

ADR 0018: a cheap near-duplicate signature over a source's cleaned text,
so corroboration can count independent facts instead of independent URLs
— a press release syndicated across several domains was previously
counted as several corroborating sources. Nullable, never backfilled for
existing rows (append-only data; those sources simply fall back to being
counted by source id, same as before this migration).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "98821d7e1a95"
down_revision: str | Sequence[str] | None = "2db80140ed4d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("content_fingerprint", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "content_fingerprint")
