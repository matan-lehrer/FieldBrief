# 0013. Framework correctness fixes: VRIO organization, entry-barrier direction, positioning evidence, insufficient evidence, internal SWOT signals

- **Status:** Accepted
- **Date:** 2026-10-22
- **Supersedes:** ADR 0009's "fold `organized` into the rationale text only" clause. Everything else in ADR 0009 (BMC block assignment, fixed positioning axes, the Five Forces/VRIO roll-up rules themselves) stands.

## Context
A deeper pass over the synthesis logic (prompted by wanting more reliable, decision-grade frameworks, not just a UI pass) found several real correctness gaps, confirmed by reading `agents/synthesis.py`, `agents/llm_schemas.py`, `reports/schema.py`, and `agents/prompts.py` directly rather than assumed from the ADRs:

1. **Entry-barrier direction is ambiguous, not just undocumented.** Every Five Forces dimension rolls into SWOT by one rule: `rating == "high" -> Threat`. ADR 0009 defines `rating` as "pressure intensity, high is bad for the company" — but nothing (the field name `entry_barriers`, the prompt, the schema) tells the model whether "high" means "barriers are high" (good for the incumbent, low threat) or "threat from new entrants is high" (bad, which is what the roll-up rule actually needs). A model reading the field name literally could rate it backwards with no warning anywhere in the pipeline.
2. **Positioning has no evidence gate.** Every other synthesis roll-up loop in `agents/synthesis.py` drops an item with zero supporting claims (`if not claim_ids: continue`) — the positioning-placement loop was the one exception, and `PositionedCompanyDraft.source_claim_indices` was the only sibling draft type without `min_length=1`. A company could be charted with no evidence behind its position at all.
3. **VRIO's `organized` is decorative** (ADR 0009's deliberate choice at the time) — a company that has a valuable, rare, inimitable resource but isn't organized to capture its value still reads as `sustained_advantage`, identical to a company that's actually winning with it. Worth revisiting now that the cost of a 5th bucket (one more case for SWOT roll-up and the UI to handle) is better understood.
4. **No "thin evidence" escape hatch.** Every Five Forces rating is forced into `low/medium/high`; every VRIO resource into one of 4 implications. The only way to avoid guessing is to omit the whole item — there's no way to say "the claims mention this, but not clearly enough to call a direction."
5. **Weaknesses (and Strengths) come only from VRIO.** Opportunities/Threats already have a free-judgment path from `events`/`market_size`/`regulation`/`bmc` claims (`other_opportunities`/`other_threats`) separate from the deterministic Five-Forces roll-up. Strengths/Weaknesses have no equivalent — an internal capability signal that isn't a `resources.advantages` claim (shrinking margins, customer concentration, a disclosed risk factor) has nowhere to go.
6. **A bare margin number could pass as cost-structure evidence.** The extraction prompt's `bmc_block` instruction didn't say a `financials.profitability` number alone isn't itself evidence of what drives cost.

## Decision

**Rename `entry_barriers` -> `threat_of_new_entrants`.** Removes the ambiguity at the source instead of hoping a longer prompt resolves it — the field now names what's actually being rated (the threat), matching every other force's naming (`buyer_power`, `rivalry`, ...: all named for the pressure, not a proxy for it). The prompt also now states the direction explicitly for this force specifically (low barriers -> high threat). Old, already-generated reports keep their stored `entry_barriers` literal — reports are immutable, and `report.html` renders any force name generically (`{{ f.force | replace("_", " ") }}`), so nothing breaks; this is a forward-only rename.

**Add a `min_length=1` guard to `PositionedCompanyDraft.source_claim_indices`**, matching every sibling draft type, and require **at least 2 placed companies with real evidence** before showing a positioning map at all (dropped to the same empty default as "no positioning" otherwise) — a one-company "chart" isn't a comparison.

**Add a 5th `competitive_implication` value, `unexploited_advantage`**, for valuable+rare+inimitable+**not** organized (ADR 0009's rejected Option 1, now adopted): the company has a real, hard-to-copy resource but isn't capturing its value. Rolls into Strengths for SWOT (the resource is real and rare/inimitable) — the VRIO table itself, and the rationale, carry the "not capturing it" signal, making it a natural `swot_refs` target for a recommendation.

**Add `"insufficient_evidence"` as a 4th Five Forces rating.** Omitted from SWOT the same way `"medium"` is, but shown honestly in the Five Forces section instead of forced into a directional guess. VRIO does **not** get an equivalent 5th "thin evidence" bucket — raising the bar for *inclusion* (via prompt instruction: omit a resource whose V/R/I can't be confidently assessed) is simpler than another enum case every consumer has to handle, and matches how every other item already escapes a forced classification (by not being included at all).

**Add `other_strengths`/`other_weaknesses`**, mirroring `other_opportunities`/`other_threats`: a free-judgment SWOT path sourced from `financials`/`risks`/`events` claims that reveal something about the company's *own* capabilities (internal) rather than the market (external) — `SWOTItem.derived_from` gains `"financials"` and `"risks"` alongside the existing values.

**Tighten the `cost_structure` BMC instruction**: the extraction prompt now says a bare margin/profitability number alone isn't cost-structure evidence — the claim must describe what actually drives cost.

## Consequences
- `ForceAssessment.force`, `ForceAssessment.rating`, `VRIOResource.competitive_implication`, and `SWOTItem.derived_from` all gained new `Literal` values — additive, backward compatible with existing stored `report_json` (Pydantic only validates on write; render templates already handle unknown literals generically).
- `PositionedCompanyDraft`'s new `min_length=1` means the synthesis model must either cite real evidence for a company's position or omit that company entirely — consistent with every other draft type, and already covered by the existing "fewer honest findings beat invented ones" prompt instruction (now extended to mention positioning explicitly).
- VRIO's `organized` field is no longer purely decorative — flows into `competitive_implication`, which flows into the SWOT roll-up. No change to the stored `organized` boolean's own meaning.
- Four new unit/integration tests cover `_vrio_implication`'s decision table, insufficient-evidence omission, the `other_weaknesses` path, and the positioning evidence gate (`tests/integration/test_agent_synthesis.py`).
