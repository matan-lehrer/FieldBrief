---
name: db-migration
description: Change the database schema with an Alembic migration. Use whenever adding or changing a table, column, index, or constraint.
---

# Database migration

1. Check the change against `docs/design.md` section 4 (Tables). If the change isn't there yet, update the design first (and write an ADR if it changes a data rule).
2. Respect the data rules:
   - `claims` are append-only: no `UPDATE`/`DELETE` paths, no columns that imply mutation.
   - `reports` are immutable once done: new versions via `parent_report_id`.
   - Don't store derived scores (confidence, reliability).
3. Update the SQLAlchemy models in `src/fieldbrief/db/`.
4. Generate: `uv run alembic revision --autogenerate -m "<short description>"`, then **read and fix** the generated file — autogenerate misses things (enums, server defaults, index names, data migrations).
5. Make it reversible: a working `downgrade()`. For anything destructive, say so in the migration docstring.
6. Add indexes for the query that motivated the change. Hot paths: `claims (company_id, field, extracted_at DESC)`, `sources (url)`.
7. Test: `uv run alembic upgrade head`, `downgrade -1`, `upgrade head` on a fresh database.
8. Never edit a migration that has already been applied/committed — write a new one.
9. Update the Tables section in `docs/design.md` in the same commit.
