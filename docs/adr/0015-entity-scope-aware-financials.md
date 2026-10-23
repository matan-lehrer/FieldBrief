# 0015. Entity/financial-scope-aware claims, split funding vs. valuation

- **Status:** Accepted
- **Date:** 2026-10-23

## Context
A real report run on Synamedia surfaced two related bugs: (1) `financials.funding_or_cap` conflated two different facts — money raised and what the company is worth — into one field, producing report text like "undisclosed funding" sitting next to a "$31.7M historical funding" figure with no way to tell whether they described the same thing; (2) the report compared a subsidiary's standalone revenue (£0-1M, "Synamedia Holdings Limited") against the parent group's headcount and other group-wide figures, because `confidence.py`'s conflict detection only matched claims by numeric *key name* (e.g. both have `amount_usd`), never by what entity/currency/period/scope the number actually describes. Both bugs produce the same failure mode: two different facts get silently treated as one.

## Options considered
1. **Leave the fields as-is, fix only at the prompt level** — ask the model to "be careful" about scope in free text. Doesn't fix the structural problem: nothing downstream can act on scope even if the model states it correctly.
2. **Add a full typed `FinancialValue` sub-schema** on `ExtractedClaim`, replacing the free-form `value: dict`. More rigid, but `value` already carries ad-hoc structured tags per field (`value.force`, `value.resource`, `value.hq`) by convention — adding one more rigid nested model just for financial fields breaks that established, lightweight pattern for no real gain.
3. **Split the conflated field, tag scope inside the existing free-form `value` dict, make comparison scope-aware.** Keeps the existing convention; the fix is in what we compare, not in a new schema layer.

## Decision
Chose option 3. `financials.funding_or_cap` is now two fields: `financials.funding` (private rounds raised) and `financials.market_cap_or_valuation` (public market cap or a private valuation). The extraction prompt asks the model to tag `value.entity_name`, `value.currency`, `value.period`, and `value.scope` ("consolidated"/"standalone") on `financials.revenue`, `financials.funding`, and `financials.market_cap_or_valuation` claims whenever the source states them — never guessed. `reports/confidence.py`'s conflict detection now compares two claims only when their scope tags agree wherever both are known; a numeric disagreement between compatible scopes is `conflicting` (unchanged), but one between claims whose scope tags actively disagree (different entity, different currency, different period) is a new status, `different_scope` — not a conflict, not corroboration, just two different facts that happen to share a field name. Rendered inline next to the figure as a short scope label ("Synamedia Holdings Limited · standalone · FY2023 · GBP") so a reader can see what a number is actually about.

## Consequences
- Report text can no longer silently compare a subsidiary's figure against its parent's, or a funding total against a valuation — either they're explicitly tagged compatible, or the report shows `different_scope` instead of a false conflict or false agreement.
- Golden eval fixtures (`evals/golden/*.yaml`) needed updating — several already conflated funding and valuation in one string value (e.g. Wiz, Stripe, Notion), which was itself an instance of the same bug one layer up.
- Cost: one more field to research per standard/deep report (two financial fields instead of one) and a few more tagging instructions in the extraction prompt — marginal token cost on calls that already happen, no new call type.
- When we would revisit this: if a third financial-scope dimension becomes common enough to need its own comparison logic (e.g. currency conversion for cross-currency comparison), promote the `value.*` convention to a typed sub-model at that point rather than before.
