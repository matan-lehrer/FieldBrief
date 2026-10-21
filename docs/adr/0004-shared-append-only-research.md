# 0004. Shared, append-only research as the cache

- **Status:** Accepted
- **Date:** 2026-10-21

## Context
The same companies appear across many reports (as target or competitor). Re-researching them is the main cost. Reports must stay trustworthy after they're delivered.

## Options considered
1. **Research owned by each report** — simple, but duplicates work and cost.
2. **One canonical, mutable report per company, refreshed by cron** — no duplication, but conflicts with user-edited competitor lists, changes reports under readers, and pays to refresh reports nobody reopens.
3. **Claims per company x field, shared and append-only; reports immutable and referencing the claims they used.**

## Decision
Option 3. Sources are global and deduped by URL; claims are append-only; a report freezes its evidence in `report_claims`; refresh creates a new report version and researches only stale or missing fields.

## Consequences
- Cache is a query: fresh claims for (company, field). Cost per report drops as usage grows.
- Old reports stay valid; "which reports used this claim?" is a cheap query.
- Storage grows (claims are never deleted) — acceptable; prune at scale if needed.
- Scores (confidence, reliability) are derived at report-build time, not stored.
