# 0009. Framework method: per-framework fields, scoring, and SWOT roll-up

- **Status:** Accepted
- **Date:** 2026-10-21

## Context
`design.md` §5 fixes the *shape* of `report_json` but not how each framework gets filled from claims, or how Five Forces / VRIO roll up into SWOT (`open-questions.md` item 2). Three sub-decisions don't fit cleanly into the schema and need a documented rule so the synthesis prompts (stage 4) have one spec to implement against.

## Options considered

**VRIO -> `competitive_implication`:** textbook VRIO sometimes splits "valuable+rare+inimitable" into *potential* vs *realized* sustained advantage depending on `organized`, which would need a 5th enum value.
1. Add a 5th value (`unexploited_advantage`) — most textbook-accurate, but adds a case every downstream consumer (SWOT roll-up, UI) must handle for a distinction that rarely changes the recommendation.
2. **Fold `organized` into the rationale text only; the 4 existing enum values stand** (not-valuable -> disadvantage, valuable-only -> parity, +rare-not-inimitable -> temporary, +inimitable -> sustained, regardless of organized) — simpler, and `organized=false` becomes an actionable recommendation ("you have the advantage, you're not capturing it") instead of a classification edge case.

**BMC block assignment:** `field-checklist.md`'s "Feeds" column only maps 4 of 9 BMC blocks to a specific field (segments, value_propositions, revenue_streams via revenue.model, key_partners); `key_resources`, `key_activities`, `channels`→already mapped, `customer_relationships`, `cost_structure` have no dedicated field.
1. Only populate blocks with a dedicated checklist field; leave the other 3 always empty — predictable, but guarantees 3 permanent gaps even when sources clearly discuss them (e.g., a 10-K discussing cost structure).
2. **Extend the ADR 0007 tagging convention to all claims**: any claim, regardless of which field it was extracted for, may carry `value.bmc_block` when the extractor judges it maps to a Business Model Canvas block. BMC synthesis groups by that tag across the company's whole claim set, not just the "Feeds: BMC" fields.

**Positioning axes:** `open-questions.md` "Later" already defers *per-industry or per-report* axis choice. Stage 0 still needs *an* MVP method.
1. Let the strong model choose axes freely per report — most flexible, but makes the positioning map non-comparable across reports and harder to eval (no fixed ground truth shape).
2. **Fix two axes for MVP: Price (low-cost <-> premium) and Differentiation breadth (niche <-> broad)**, built from `pricing.model`, `offering.value_prop`, `offering.products`, `customers.segments`. Revisit (per-industry axes) stays on the Later list.

## Decision
Option 2 in all three cases, plus these roll-up rules:

**Five Forces -> Opportunities/Threats:** a force's `rating` already means "pressure intensity" (`high` = bad for the company). `high` -> Threat, `low` -> Opportunity, `medium` -> omitted from SWOT (still shown in the Five Forces section, just not force-fed into SWOT on weak signal).

**VRIO -> Strengths/Weaknesses:** `sustained_advantage` / `temporary_advantage` -> Strength; `competitive_disadvantage` -> Weakness; `parity` -> omitted from SWOT (shown in VRIO section only) unless the synthesis model has explicit evidence a competitor's version is notably stronger, in which case it may still emit a Weakness with `derived_from: "vrio"`.

**Other SWOT sources:** `events.recent`, `market.size_growth`, `environment.regulation` claims feed Opportunities/Threats directly by model judgment (`derived_from: "events" | "market_size" | "regulation"`); BMC content can occasionally surface a finding (`derived_from: "bmc"`) but isn't a primary SWOT source.

**Recommendations:** 3-7 per report (bounded output, not unbounded model choice), each with its own `claim_ids` (not just inherited via `swot_refs`) and at least one `swot_refs` entry — a recommendation with no linked SWOT item is a synthesis bug, not a valid output.

## Consequences
- Synthesis prompts (stage 4) implement exactly these rules — no per-report variation in which ratings/implications map to which SWOT bucket.
- `organized=false` VRIO resources need their rationale to say so explicitly, since the enum no longer carries that signal.
- BMC's 3 unmapped blocks (`customer_relationships`, `key_activities`, `cost_structure`) will often be gaps for companies with thin sourcing — expected and shown as such, not fabricated.
- Fixed positioning axes mean the map is comparable across every MVP report; per-industry axes (deferred) would break that comparability and needs its own decision when picked up.
