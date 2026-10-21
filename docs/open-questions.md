# Open questions

Undecided items. Don't decide these silently in code — raise them, decide, then record the decision in `design.md` (and an ADR if it's significant).

## Stage 0 (blocking implementation)
1. **Field checklist** — approve the DRAFT (the two sub-items are resolved, see `field-checklist.md` "Resolved" and ADR 0007).
2. **Framework method** — per framework: required fields, scoring method, how findings roll up into SWOT (Five Forces -> O/T, VRIO -> S/W), how positioning axes are chosen.
3. **Agent graph** — nodes, state schema, edges, fan-out unit (company x field?), stop conditions (retry cap, cost budget, time budget, confidence threshold), partial-report rules.
4. **Eval plan** — golden set (~10 companies, mix of public/private, sizes, industries), ground-truth format, metric definitions mapped to each NFR.
5. **Model picks** — which cheap and strong models; verify current prices before committing to the cost NFR.

Report schema is decided — see `design.md` §5 and ADR 0007.

## Later
- Competitor discovery method: search-based, LLM-proposed, or both?
- How many fetch attempts before a field is "not found"?
- Positioning map: fixed axes per industry, or chosen per report?
- Auth beyond a single API key (when there's more than one user).
- Langfuse self-hosting footprint — fits in local compose, or run it separately?
