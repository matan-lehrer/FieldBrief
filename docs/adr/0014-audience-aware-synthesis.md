# 0014. Executive brief, comparison table, and audience-aware synthesis

- **Status:** Accepted
- **Date:** 2026-10-22

## Context
The report led with Five Forces/BMC/VRIO/positioning detail and ended with SWOT + recommendations — no top-level "here's what actually matters" summary, no side-by-side comparison across companies on shared dimensions, and no way to steer the *interpretation* of findings toward a specific reader (an analyst, an investor, the target's own leadership, a rival, a sales/partnership prospect) without re-researching anything. Matan's ask was explicit that this must be **one shared research pass, interpreted differently per audience** — never a second autonomous agent or research pipeline per audience, and never a new paid service or investment-platform feature (no forecasts, no buy/sell ratings).

## Decision

**Executive brief.** The synthesis call now also returns 3-5 of the most material, well-supported findings (`ExecutiveFindingDraft`: `statement` + one-sentence `why_it_matters`, cited like everything else). Rendered first in the report (Stage 6), before the framework detail — a reader gets the headline before the evidence trail.

**Comparison table.** A dimension x company grid, assembled **deterministically in `agents/synthesis.py`** from already-built `CompanySnapshot` data — no new LLM call, no new research. Dimensions are the field-checklist.md fields researched for *both* target and competitors (`profile.description`, `offering.products`, `customers.segments`, `revenue.model`, `financials.revenue`, `financials.funding_or_cap`) — a fair, evidence-backed comparison on things every company in the report actually has a shot at having data for. A dimension no company has any data for is dropped entirely, not shown as an empty row. Each cell reuses the existing `Citation` type (claim_ids + confidence) rather than inventing a parallel "comparison claim" concept — the comparison table is a *view* over evidence that already exists in `companies`, not a new evidence category.

**Audience.** `CreateReportRequest.audience` (`analyst | investor | leadership | competitor | sales_partnership`, default `analyst`) is persisted on the `Report` row (not just a transient job-message field — same reasoning `competitor_count` historically needed `REPORT_COMPETITORS` for) so a refresh can read it back. It flows through `run_report` -> `run_synthesis` -> `framework_synthesis_prompt`, which steers the model's framing for the executive brief and a new `audience_implications` section (relevance to this specific reader, explicit assumptions, follow-up questions — cited, like everything else; omitted entirely if the claims don't support it). **Research itself never sees `audience`** — `agents/nodes.py`'s field checklist, search queries, and extraction are completely unaffected; only the one synthesis call's prompt and its two new output sections change. This is the literal implementation of "research once, interpret per audience": one new parameter on one existing call, not a second pipeline.

## Consequences
- `ReportJSON` gains `executive_brief`, `comparison`, and `audience_implications` (all additive, default to empty/`None` — no change to how an existing stored report renders). `ReportMeta` gains `audience`, defaulting to `"analyst"` for reports that predate this.
- A new Alembic migration (`d37ccfd2f84a`) adds `reports.audience` (`report_audience` Postgres enum, `NOT NULL DEFAULT 'analyst'`) — existing rows get the default, no backfill needed.
- `reports/render.py`'s `collect_claim_ids` resolves claim_ids from all three new sections, so Stage 6's rendering can show sources for them the same way as everything else — no separate resolution path.
- The comparison table's dimension list is fixed for MVP, matching the existing fixed-positioning-axes precedent (ADR 0009) — per-report dimension choice is a Later item if it turns out too rigid in practice, not decided here.
- No new LLM call anywhere in this change — cost per report is unchanged (ADR 0008's single-synthesis-call model still holds; the call just returns more fields).
