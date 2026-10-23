# 0016. Claim typing and anti-overreach synthesis rules

- **Status:** Accepted
- **Date:** 2026-10-23

## Context
Reviewing a real report (Synamedia) alongside Matan's broader quality request surfaced a validation gap: the pipeline cites every claim to a source, but it has no way to tell a filed, independently-reportable fact apart from a company's own promotional framing, an inference the model drew from other facts, or outright speculation. All four end up as a plain "statement" with a source link, read by the user with equal weight. Concretely: a customer-announcement press release isn't proof of product-market fit; a patent filing doesn't by itself establish a sustained competitive moat; market-level growth figures don't establish one company's own margins; a competitor-directory listing doesn't establish rivalry intensity; a divestiture doesn't by itself establish financial distress. Citing a source for each of these is necessary but not sufficient — the claim can still be over-interpreted in synthesis.

## Options considered
1. **Prompt-only fix** — tell the synthesis model not to overreach, with no structural change. Cheap, but unverifiable and silently regresses whenever the prompt is touched again.
2. **A full fact-checking sub-agent** that re-verifies every finding against its claims. Explicitly out of scope — the current ask is "improve retrieval, evidence selection, validation, and presentation before adding agents."
3. **Tag claim strength at extraction time, enforce the distinction in the synthesis prompt, and add a deterministic fallback check.** Structural (the tag exists whether or not the model remembers to use it in prose) without a new agent or LLM call.

## Decision
Chose option 3. `ExtractedClaim` gains a required `claim_type`: `reported_fact`, `attributed_claim`, `analytical_interpretation`, or `hypothesis` — the extraction prompt is told to pick the weakest honest label (a company's own press release is an `attributed_claim`, not a `reported_fact`, even when stated plainly). Stored on `Claim.claim_type` (nullable — pre-migration claims have no classification, which is a legitimate unknown). The synthesis prompt sees each claim's type and is told explicitly not to over-interpret attributed claims, patents, market data, directory counts, or divestitures into stronger conclusions than they support. As a deterministic backstop — since an LLM can still forget a prompt instruction — `synthesis.py` mechanically flags any executive-brief finding or recommendation whose *every* cited claim is `analytical_interpretation` or `hypothesis` (none are `reported_fact`/`attributed_claim`) into `report_json.meta.overreach_flags`, rendered as a visible "limited evidence" caveat next to that specific item in both HTML and PDF — never silently dropped, never presented at full confidence.

## Consequences
- Findings/recommendations resting entirely on interpretation are now visibly distinguished from well-evidenced ones, without adding a verification pass or a new LLM call.
- `claim_type` being required on `ExtractedClaim` means every existing test fixture constructing one needed updating — a one-time mechanical cost, paid now.
- The backstop is coarse (all-weak vs. has-one-strong-claim, not a weighted blend) — deliberately simple; a claim with even one `reported_fact`/`attributed_claim` behind it isn't flagged, so this catches only the clearest overreach cases, not every possible overstatement.
- When we would revisit this: if the model's own self-qualification (the prompt instruction) turns out reliable in practice across real runs, the deterministic backstop could be loosened; if overreach still slips through with a `reported_fact`/`attributed_claim` claim mixed in, a weighted or per-claim confidence signal would be the next step.
