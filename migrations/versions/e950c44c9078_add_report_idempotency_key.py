"""add report idempotency key

Revision ID: e950c44c9078
Revises: e3108b7f716e
Create Date: 2026-10-22 09:58:36.477949

Stage 6: design.md §8 — "POST /reports accepts an Idempotency-Key header."
Scoped per user (NULLs don't collide in a Postgres unique index, so existing
CLI-created reports with no key are unaffected).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e950c44c9078"
down_revision: str | Sequence[str] | None = "e3108b7f716e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("idempotency_key", sa.String(), nullable=True))
    op.create_index(
        "ux_reports_user_idempotency_key",
        "reports",
        ["user_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_reports_user_idempotency_key", table_name="reports")
    op.drop_column("reports", "idempotency_key")
