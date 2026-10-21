# Open questions

Undecided items. Don't decide these silently in code — raise them, decide, then record the decision in `design.md` (and an ADR if it's significant).

## Stage 0 — all resolved
Field checklist approved (`field-checklist.md`, ADR 0007). Report schema: `design.md` §5, ADR 0007. Framework method: `design.md` §6, ADR 0009. Model picks: `design.md` "LLM gateway", ADR 0008. Agent graph: `design.md` §7, ADR 0010. Eval plan: `design.md` §14, ADR 0011.

## Later
- Competitor discovery method: search-based, LLM-proposed, or both?
- How many fetch attempts before a field is "not found"?
- Positioning map: fixed axes per industry, or chosen per report?
- Auth beyond a single API key (when there's more than one user).
- Langfuse self-hosting footprint — fits in local compose, or run it separately?
