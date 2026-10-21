# Open questions

Undecided items. Don't decide these silently in code — raise them, decide, then record the decision in `design.md` (and an ADR if it's significant).

## Stage 0 (blocking implementation)
1. **Field checklist** — approve the DRAFT (the two sub-items are resolved, see `field-checklist.md` "Resolved" and ADR 0007).
2. **Eval plan** — golden set (~10 companies, mix of public/private, sizes, industries), ground-truth format, metric definitions mapped to each NFR.

Report schema is decided — see `design.md` §5 and ADR 0007. Framework method is decided — see `design.md` §6 and ADR 0009. Model picks are decided — see `design.md` "LLM gateway" and ADR 0008. Agent graph is decided — see `design.md` §7 and ADR 0010.

## Later
- Competitor discovery method: search-based, LLM-proposed, or both?
- How many fetch attempts before a field is "not found"?
- Positioning map: fixed axes per industry, or chosen per report?
- Auth beyond a single API key (when there's more than one user).
- Langfuse self-hosting footprint — fits in local compose, or run it separately?
