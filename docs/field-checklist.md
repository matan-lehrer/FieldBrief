# Field checklist

**Approved 2026-10-21.** The facts the planner must find per company. Drives the gap check, the cache (`claims.field`), coverage metrics, and framework inputs — don't add/remove/retier a field without checking ripple effects in `design.md` §5-7.

**T** = target, **C** = competitor (light subset). Tiers are cumulative: Quick < Standard < Deep.

| Field | Holds | Feeds | Tier | T/C |
|---|---|---|---|---|
| `profile.description` | What the company does | Overview | Quick | T, C |
| `profile.hq_founded` | HQ, founding year | Overview | Quick | T, C |
| `profile.employees` | Headcount | Scale, positioning | Quick | T, C |
| `offering.products` | Main products/services | BMC, positioning | Quick | T, C |
| `customers.segments` | Who buys | BMC, positioning | Quick | T, C |
| `revenue.model` | How it makes money | BMC | Quick | T, C |
| `financials.revenue` | Latest revenue + growth | Snapshot | Quick | T, C |
| `events.recent` | Key news, last 12 months | SWOT (O/T) | Quick | T |
| `pricing.model` | Price points, tiers, free plan | Positioning, Five Forces | Standard | T, C |
| `offering.value_prop` | Claimed differentiation | BMC, VRIO | Standard | T, C |
| `channels.go_to_market` | Sales motion | BMC | Standard | T |
| `financials.profitability` | Margins / net income / burn | Snapshot, SWOT | Standard | T |
| `financials.funding` | Private funding raised to date | Snapshot | Standard | T, C |
| `financials.market_cap_or_valuation` | Market cap (public) / valuation (private) | Snapshot | Standard | T, C |
| `resources.advantages` | Brand, IP, network effects, data, switching costs | VRIO | Standard | T |
| `market.forces` | Buyers, suppliers, substitutes, entry barriers | Five Forces | Standard | T |
| `environment.regulation` | Relevant regulation | PESTEL-lite (Threats) | Standard | T |
| `market.size_growth` | Market size, growth | Opportunities | Deep | T |
| `partners.key` | Key partners / suppliers | BMC | Deep | T |
| `strategy.moves` | M&A, expansions, launches | SWOT, recommendations | Deep | T, C |
| `risks.disclosed` | Risk factors (10-K) | Threats | Deep | T |

## Rules
1. **"Not found" is a valid answer.** After N attempts, store an explicit unknown claim. No endless retries; the report shows the gap.
2. **Competitors get the light subset** (T, C rows) — enough for comparison and positioning.
3. **Freshness:** `events.*` ~7 days · `financials.*` until the next filing · everything else 30 days.

## Resolved
- `market.forces` stays one field; claims tag `value.force` to identify which of the five forces they support (ADR 0007).
- `resources.advantages` stays one field; claims tag `value.resource` to identify which resource they support (ADR 0007).
- `financials.funding_or_cap` split into `financials.funding` and `financials.market_cap_or_valuation` — the two were different facts (money raised vs. what the company is worth) and conflating them produced incoherent report text (ADR 0015). `financials.revenue`, `financials.funding`, and `financials.market_cap_or_valuation` claims tag `value.entity_name`/`value.currency`/`value.period`/`value.scope` when the source states them, so confidence scoring can tell a subsidiary's standalone figure apart from its parent's consolidated one instead of silently comparing them as the same number (ADR 0015).
- Gap display goes through `field_label()` (this file's own key -> `FIELD_LABELS`), not the raw dotted key. A tagged field (`market.forces`, `resources.advantages`) exhausted with *some* but not enough distinct tags reports reason `partial`, not `not_found` (ADR 0017).
