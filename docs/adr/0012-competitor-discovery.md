# 0012. Competitor discovery: search-grounded extraction, not LLM-proposed

- **Status:** Accepted
- **Date:** 2026-10-22

## Context
`open-questions.md`'s "Later" list left this open: "Competitor discovery method: search-based, LLM-proposed, or both?" Stage 4 needs an answer to populate `COMPANY_COMPETITORS` and fan out research over competitors.

## Options considered
1. **LLM-proposed** — ask the model directly "who competes with X," no search. Fast, cheap, but ungrounded: nothing to cite, and the model's training-data knowledge of competitors can be stale or wrong for anything recent (a company that pivoted, a new entrant). Conflicts with hard constraint 5 ("every claim cites a source").
2. **Search-grounded extraction** — search "`<company>` competitors" / "`<company>` alternatives," fetch top results (comparison sites, "X vs Y" articles routinely name competitors directly), extract named companies via the same cheap-tier extraction call already used for claims. Each candidate traces back to a fetched page, consistent with everything else in the pipeline.
3. **Both** — search-grounded, with the model allowed to add well-known competitors it's confident about even without a source. Keeps the gap-filling benefit of option 1 while keeping option 2's grounding as the default. Rejected for stage 4: adds a second, differently-trusted code path for the same output (`COMPANY_COMPETITORS` doesn't currently distinguish "cited" from "model's own knowledge" rows), and option 2 alone is enough to hit the "a full standard report runs" done-when. Revisit if search-grounded discovery turns out too thin for obscure companies.

## Decision
Option 2. `agents/competitors.py`: search `"<company> competitors"`, fetch the top result, extract named companies with the cheap model (reusing the extraction schema pattern, not a new one), resolve each named company via Wikidata (`fetch/wikidata.py`, same `pick_company_like` disambiguation already built), and write `COMPANY_COMPETITORS` rows with `origin = "discovered"`.

## Consequences
- `COMPANY_COMPETITORS` has no `claim_id` column (design.md §4's schema predates this decision) — provenance for *why* a competitor was picked lives only in the source page, not a stored claim. Acceptable for MVP; revisit if competitor-discovery accuracy becomes an eval concern (ADR 0011) and needs its own citation trail.
- A company with thin search coverage (no comparison articles) may surface zero or few competitors — shown as fewer competitors than the tier's target count, not padded with guesses.
- `origin = "user"` (the other enum value) is for a user editing the list by hand — not built yet (needs the API/UI, stage 6-7); CLI runs always get `"discovered"`.
