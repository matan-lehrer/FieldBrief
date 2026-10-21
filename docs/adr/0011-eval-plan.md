# 0011. Eval plan: golden set, ground truth format, metrics

- **Status:** Accepted
- **Date:** 2026-10-21

## Context
Stage 5's "Done when" is "an eval results table is committed," and `CLAUDE.md` says quality is measured by the eval harness, not unit tests — but which companies, what a "correct" answer looks like, and which metric maps to which NFR were all still open (`open-questions.md` item 5, now 2).

## Options considered
**Golden set size/composition:** `roadmap.md` already set the target (~10 companies, mix of public/private, sizes, industries) — the open part was *which* companies.
1. Pick companies as needed, ad hoc, when eval work starts (stage 5) — defers the decision to when it's more work to change (ground-truth files would already be partly written).
2. **Fix the list now**, in this ADR, chosen to deliberately stress the hardest parts of the design: entity resolution (near-duplicate names), financial variety (public filings vs. no filings), and recent-event sourcing (an active news story).

**Ground truth for subjective frameworks (SWOT, VRIO, positioning):** these are judgment, not fact, so exact-match ground truth doesn't apply.
1. Exact-match reference text — brittle; two reasonable analysts would phrase a SWOT point differently.
2. **Key-point checklist per framework section**, graded by an LLM judge (did the report's output cover these points, roughly) rather than string match. Facts (`profile.*`, `financials.*`) still get exact/near-exact ground truth, since those *are* checkable facts.

## Decision

**Golden set (10 companies, `evals/golden/<slug>.yaml`):**
| Company | Public/Private | Industry | Why it's in the set |
|---|---|---|---|
| Wix | Public | Website builder SaaS | Already the worked example (`CLAUDE.md`, roadmap stage 3/4) |
| Wiz | Private | Cybersecurity | Near-duplicate name vs. Wix — stresses entity resolution |
| Shopify | Public | E-commerce SaaS | Large-cap, heavy filings, many competitors |
| Stripe | Private | Fintech / payments | Large private co., funding-not-market-cap path |
| Notion | Private | Productivity SaaS | Private, consumer+enterprise crossover positioning |
| Figma | Public | Design SaaS | Recently public — tests freshly-changed `financials.funding_or_cap` |
| Canva | Private | Design SaaS | Non-US HQ, direct Figma competitor — tests the competitor graph |
| Airtable | Private | Productivity SaaS | Mid-size, thinner public sourcing than the above |
| DoorDash | Public | Logistics marketplace | Non-SaaS revenue model, thin margins — stresses `financials.profitability` |
| Oatly | Public | Consumer packaged goods | Non-tech, public financial distress — stresses `events.recent` / Threats |

**Ground truth format** (`evals/golden/<slug>.yaml`): a human-curated file per company with
- `facts`: exact/near-exact expected values for checkable fields (`profile.hq_founded`, `financials.revenue` with a tolerance band, `financials.funding_or_cap`), each with the source URL used to verify it.
- `competitors`: the expected competitor set (for competitor-discovery eval, not just research).
- `key_points`: a short checklist per framework (`five_forces`, `vrio`, `swot`, `recommendations`) of points a *good* report should hit — graded by LLM judge, not string match.
- `known_gaps`: fields expected to come back "not found" even for a careful human researcher (e.g., a private company's unpublished margins) — a report that *invents* a value here is a precision failure, not a coverage win.

**Metrics, mapped to `design.md` §1 NFRs:**
| NFR | Metric | How |
|---|---|---|
| Accuracy (>=90% claims supported) | **Claim precision** | Sample claims per report; verify the cited source's snippet actually supports the claim (fetch + substring/LLM-judge check) |
| Accuracy (extension) | **Citation validity** | % of findings (SWOT items, recommendations, framework assessments) with >=1 resolvable `claim_id` — catches orphaned/fabricated findings |
| Accuracy (extension) | **Framework quality** | LLM judge scores each framework's output against `key_points`, 1-5; tracked for tuning, not a hard gate |
| Cost (<=$0.50 standard) | **Cost per report** | Read `cost_usd` from the gateway's cost log (ADR 0005) per golden-set run at standard tier; report mean + p95 |
| Latency (§1 targets) | **Time to milestone** | Timestamps from the SSE/progress stream (ack, first section, full report); report p50/p95 |
| Reliability (>=99%/95%) | **Completion rate** | Run each golden company 3x; % reaching `done`/`partial` (any completion) vs. `done` specifically |

**Scope for MVP:** no CI gate on these numbers — the harness produces a committed results table (stage 5's literal "Done when"), reviewed by hand. A regression-gate gets added only if/when eval runs become routine enough to be worth automating (not now — "buildable solo in reasonable time").

## Consequences
- The golden set is fixed, reviewable, and includes the Wix/Wiz collision on purpose — if entity resolution can't tell them apart, that's a real bug the eval will catch early.
- Ground truth files are hand-written, one-time cost per company (~10 files); `known_gaps` prevents the harness from penalizing honest "not found" answers, keeping it aligned with hard constraint 5 ("unknown is acceptable, invented is not").
- No automated CI gate means a regression can land without failing a build — acceptable for a solo MVP; revisit if eval runs become frequent enough to automate.
- All 10 companies change over time (filings, funding rounds); ground truth files will need periodic refresh — not a blocker for stage 5, just maintenance.
