# 0017. Honest gap reasons, human field labels, and competitor-coverage flags

- **Status:** Accepted
- **Date:** 2026-10-23

## Context
Three related honesty gaps in how a finished report represents what it didn't find: (1) the gaps section printed raw dotted field keys (`financials.funding_or_cap`) instead of anything a reader recognizes; (2) a tagged multi-dimension field (`market.forces`, `resources.advantages`) that found *some* evidence but not enough breadth to call resolved was lumped into the same flat "not found" as a field with zero evidence — so a report could show a real "rivalry: high" Five Forces assessment while also listing `market.forces (not found)` in the gaps section, which reads as contradictory even though it isn't (some forces were found, not all); (3) when competitor discovery returned zero results, the comparison table still rendered — with only the target company's column populated — looking like a legitimate one-row comparison instead of what it actually was: competitor research that failed.

## Options considered
1. **Leave gaps as raw keys, add a lookup only in the template.** Works for the label problem alone, doesn't touch the partial/not-found conflation or the fake single-company comparison.
2. **Score competitor coverage as a continuous number** (e.g. `competitors_found / competitors_requested`). More precise, but a raw ratio needs the reader to do math; a report should state the plain consequence.
3. **A small reason taxonomy (`not_found`/`partial`/`budget_cutoff`) plus a discrete `competitor_coverage` enum (`full`/`partial`/`none`/`not_requested`), both rendered as plain text/banners.** Matches how every other "did we have enough evidence" signal in this codebase is surfaced (`Confidence.status`, not a bare float) — chose this for consistency.

## Decision
- `agents/field_checklist.py` gains `FIELD_LABELS` + `field_label()`, registered as a Jinja global in `reports/render.py`; both templates render gap fields through it instead of the raw key.
- `gap_check_node` (`agents/nodes.py`) computes a `reason` per exhausted gap: for untagged fields, `not_found` as before; for tagged multi-dimension fields, `partial` when some verified claims exist but not enough distinct tags to call the field resolved, `not_found` only when there's genuinely nothing. `orchestrator.py` carries that reason through instead of hardcoding `not_found` for every exhausted gap.
- `orchestrator.py` computes `competitor_coverage` (`_competitor_coverage()`, a pure function) from how many competitors were requested vs. actually confirmed: `not_requested` (a deliberate single-company report), `none` (zero confirmed), `partial` (fewer than requested), `full`. Stored in `report_json.meta.competitor_coverage`. Both templates render a visible "competitor research incomplete" / "coverage is partial" note for `none`/`partial`, and the comparison table only renders when there's more than one company in the report at all — a confirmed-zero-competitors report no longer shows a single-column table dressed up as a comparison.

## Consequences
- A reader sees "Five Forces coverage (partial)" next to a real partial Five Forces section instead of a flat "not found" that looks like it contradicts what's shown elsewhere on the page.
- A zero-competitor report is now unambiguous about why there's no comparison, instead of silently rendering a comparison table that only has one company in it.
- `not_applicable` and `conflicting` as additional gap reasons (raised as options while scoping this) were deliberately **not** added here: `not_applicable` would need a reliable public/private signal the data model doesn't currently carry, and a numeric "conflict" is already visible on the field itself via `Confidence.status` (ADR 0015) rather than needing a second signal on the gap. Left for a future ADR if a real need appears.
- When we would revisit this: if `not_applicable` becomes groundable (e.g. a reliable public/private company signal lands for another reason), extend the reason taxonomy then rather than guessing now.
