# 0010. Agent graph: nodes, state, fan-out, stop conditions, partial reports

- **Status:** Accepted
- **Date:** 2026-10-21

## Context
ADR 0001 decided *that* the pipeline is one orchestrated LangGraph per report. It didn't decide the node list, the fan-out unit (`open-questions.md` asked "company x field?"), the state schema, the loop's stop conditions, or when a report is "done" vs "partial" (`open-questions.md` item 4, now 2).

## Options considered

**Fan-out unit for research:** the checklist (`field-checklist.md`) is keyed by company x field; the question is what LangGraph actually parallelizes on.
1. **Fan out once, per (company, field) pair, flat** — maximum parallelism, but `design.md` §8's "Analyze — per company as soon as its research is done (pipelined)" needs per-company completion signals, which a flat fan-out can't give without extra bookkeeping.
2. **Two-level fan-out: outer per company, inner per field within that company's research** — each company's branch runs its own plan/research/verify/gap-check loop independently and in parallel across companies; a company's branch finishes (and can move to `analyze`) as soon as *its* fields are resolved, without waiting on slower competitors. A join node waits for all company branches before cross-company synthesis.

**Where VRIO/BMC get computed:** both are single-subject frameworks (`field-checklist.md`: `resources.advantages` is target-only; BMC's partner/segment inputs are mixed but BMC itself describes one business model).
1. Compute for every company — wasted work; competitors don't need a BMC or VRIO breakdown, only comparison data (Five Forces, positioning).
2. **Compute only for the target**, inside its `analyze` call; competitor `analyze` calls only build `CompanySnapshot`.

**Speculative pre-warm (`design.md` §8 step 2) vs the report graph:** pre-warm runs before a report exists (before the user confirms competitors/tier).
1. Invoke the full report graph with a placeholder report row — needs a report to exist before the user asked for one; complicates `REPORTS.status`.
2. **Pre-warm runs just the `plan` -> `research` -> `verify` subgraph, keyed by company list only, no `report_id`.** Claims are cached by company x field (ADR 0004), not by report, so no report row is needed to warm the cache; the later full run's `plan` node simply finds fresher claims already sitting there.

## Decision

**Nodes:** `plan`, `research`, `verify`, `gap_check` (conditional), `analyze`, `synthesize`, `deliver`.
- `plan(company_id)` — diffs the tier's required fields (`field-checklist.md`) against fresh claims for that company; emits a gap list `(field, attempts)`.
- `research(company_id, field)` — search -> fetch -> dedup -> extract (cheap model); writes claims (tagged per ADR 0007/0009 where applicable).
- `verify(company_id, field)` — cheap model checks each new claim against its snippet; unverified/low-confidence claims stay as gaps.
- `gap_check(company_id)` — conditional edge: loop back to `plan` (narrowed to remaining gaps, `retry_round += 1`) while under all three caps below and gaps remain; otherwise record any field at its attempt cap as an explicit "not found" claim (`field-checklist.md` rule 1) and advance to `analyze`.
- `analyze(company_id)` — one strong-model call: builds `CompanySnapshot`; additionally VRIO + BMC blocks **iff `is_target`**.
- `synthesize(report_id)` — one strong-model call, after every company's `analyze` completes (join): Five Forces, positioning, SWOT (roll-up per ADR 0009), recommendations.
- `deliver(report_id)` — persists `report_json` + `report_claims`, sets final status, emits the terminal progress event.

Fan-out is **two-level**: outer fan-out over companies (target + competitors) runs independent `plan -> research x fields -> verify x fields -> gap_check` loops in parallel; each company's branch reaches `analyze` on its own schedule (pipelined). `synthesize` is the join: it waits for every company's `analyze` to finish, since Five Forces and positioning need the whole set.

**State** (checkpointed in Postgres per ADR 0001; nodes pass IDs, not payloads):
```python
class Gap(BaseModel):
    field: str
    attempts: int


class CompanyBranchState(BaseModel):
    company_id: UUID
    is_target: bool
    gaps: list[Gap]
    retry_round: int
    analyzed: bool


class ReportState(BaseModel):
    report_id: UUID
    tier: Tier
    stage: Stage  # RESOLVING..RENDERING, drives SSE (design.md "Progress UX")
    companies: list[CompanyBranchState]
    cost_spent_usd: float
    cost_budget_usd: float  # from the tier's approx. cost (design.md §1)
    started_at: datetime
    time_budget_s: int  # from the tier's approx. time (design.md §1)
```

**Stop conditions** (every loop capped, per `CLAUDE.md`), checked in `gap_check`, any one trips it:
1. **Retry round cap**, per tier: quick = 1, standard = 2, deep = 3 rounds of research+verify.
2. **Per-field attempt cap**: 2 attempts (`field-checklist.md` rule 1) before that field becomes an explicit "not found" claim.
3. **Cost budget**: the gateway (ADR 0005 step 3) already stops/downgrades near the per-report budget; `gap_check` also stops looping once `cost_spent_usd` is within 10% of `cost_budget_usd`.
4. **Time budget**: elapsed time since `started_at` exceeds the tier's time budget.

Hitting 1 or 2 for a *specific field* just means that field is "not found" (normal, per field-checklist rule 1) — the company's branch still proceeds to `analyze`. Hitting 3 or 4 is report-wide: every company branch still in `gap_check` moves on immediately, and any field that was cut off *before* reaching its own attempt cap is marked a budget gap, not a not-found gap (see below).

**Pre-warm** runs `plan -> research -> verify` only, per company, with no `report_id` — just populates the shared claims cache (ADR 0004). The full graph's `plan` node then finds the warmed claims already fresh and skips re-researching them.

**Partial vs. done:** `REPORTS.status`:
- **done** — every required field (by tier) for every company either has a verified claim or an explicit "not found" claim reached through its own attempt cap. Gaps may exist and are shown, but every one of them is a clean "not found," not a cutoff.
- **partial** — `synthesize`/`deliver` ran successfully, but at least one required field never reached its attempt cap because a report-wide cap (cost or time budget) cut the loop short first. The report still renders with whatever claims exist; the UI distinguishes "not found" from "ran out of time/budget before checking" (`CoverageGap` doesn't currently carry this distinction — add a `reason: "not_found" | "budget_cutoff"` field to `CoverageGap` in `design.md` §5 when implementing).
- **failed** — a node raises after its own repair retry (gateway step 6) with no checkpoint to usefully resume from (e.g., Postgres itself is down). Reserved for infrastructure failure, not missing data — missing data is always `partial`, never `failed` (hard constraint 5: "never a crash for the user").

## Consequences
- Two-level fan-out means N+1 join points per report (one gap_check per company, one synthesize join) — more graph edges than a flat design, but gives real per-company pipelining without extra bookkeeping.
- `CompanyBranchState` duplicated per company in `ReportState` is small (IDs + counters), consistent with "pass IDs not payloads."
- Pre-warm having no `report_id` means it can't appear in `REPORTS` or be cancelled like a report job — it's a best-effort cache warm; if it never finishes, the full run just does the research itself.
- `design.md` §5's `CoverageGap` needs a `reason` field (noted above) — track as a follow-up when §5 is implemented in stage 3/4, not a reason to redo this ADR.
