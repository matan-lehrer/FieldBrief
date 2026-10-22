# 0008. Model picks: Gemini 2.5 Flash (cheap) + Gemini 2.5 Pro (strong)

- **Status:** Accepted
- **Date:** 2026-10-21

## Context
`llm/` (the LLM gateway, ADR 0005) routes by `task` + tier to a cheap model (extraction, verification) or a strong model (synthesis only, never in a loop — `CLAUDE.md`). The cost NFR caps a standard report at <= $0.50; `design.md` §2 assumes ~250K tokens/standard report. Prices change often and must be checked against the live provider page before committing spend (`open-questions.md` item, now resolved here).

Pricing below is aggregator-sourced (several `anotherwrapper.com` / `morphllm.com` pages, dated mid-to-late 2026) since the official pricing pages weren't directly fetchable in this session. Re-check `ai.google.dev/gemini-api/docs/pricing` before relying on these for a production budget — treat the numbers as "best effort as of 2026-10-21," not a guarantee.

## Options considered
1. **Anthropic (Haiku 4.5 cheap / Sonnet 4.6 strong)** — $1/$5 and $3/$15 per M tokens (in/out). At the assumed token split (~200K cheap, ~50K strong, 70/30 in/out), cheap alone costs ~$0.44 and strong ~$0.33 → ~$0.77/report. Over budget.
2. **OpenAI (GPT-5 mini cheap / GPT-5 strong)** — mini is cheap ($0.25/$2.00) but no verified price was found for full GPT-5 in this session; picking a strong model without a verified price violates the point of this decision.
3. **Google (Gemini 2.5 Flash cheap / Gemini 2.5 Pro strong)** — $0.30/$2.50 and $1.25/$10.00 per M tokens. Same split: cheap ~$0.19, strong ~$0.19 → ~$0.39/report, with margin for retries and corroboration calls. One provider, one API key, consistent with "the LLM API key" (singular) in `CLAUDE.md`.
4. **Gemini 2.5 Flash-Lite instead of Flash for cheap** — cheaper still ($0.10/$0.40 → ~$0.04), but Flash-Lite is the weakest tier; risks the >=90% claim-accuracy NFR for structured extraction. Rejected for accuracy, not cost.

## Decision
Option 3. Cheap tier: **Gemini 2.5 Flash**. Strong tier: **Gemini 2.5 Pro**. Routed through LiteLLM like any other provider — switching later is a gateway config change, not a code change (ADR 0005).

Estimated standard-report cost at the design.md §2 token assumption: ~$0.39, inside the $0.50 cap with ~20% headroom for the capped retry loop and corroboration re-fetches.

## Consequences
- One provider API key for MVP (simpler setup, one `.env` entry), per the "only paid item" constraint.
- The estimate rests on an assumed 200K-cheap / 50K-strong, 70/30 in/out token split — not yet measured. The eval harness (stage 5) replaces this assumption with real cost logs from the gateway (ADR 0005 step 7); re-run this ADR's numbers once real data exists.
- If Google prices rise or Gemini accuracy underperforms the golden set in stage 5, swap via gateway config — no code change, per the seam ADR 0005 established.
- Revisit before any cost-sensitive milestone (stage 5 evals, stage 8 ship) by checking live prices again.

## Addendum (2026-10-22): 2.5 Flash/Pro retired, keys swapped live
First real run against the live Gemini API (Matan's key, stage 8) hit `404 This model models/gemini-2.5-flash is no longer available to new users`. Google had retired both 2.5 Flash and 2.5 Pro for new API keys by this date. Verified live against the real API (not aggregator pricing pages, per this ADR's own caveat about them):
- **Cheap tier -> `gemini/gemini-3.8-flash`.** Confirmed working on a free-tier key.
- **Strong tier -> `gemini/gemini-3.1-pro-preview`.** Confirmed as the correct current model string (`gemini-3-pro-preview` 404s with the same "no longer available" error), but a **free-tier key gets a hard 0 quota for any Pro-tier model** — `generate_content_free_tier_requests, limit: 0` — not a rate limit, a wall. Billing must be enabled on the underlying Google Cloud project for any strong-tier call to succeed; no amount of retrying gets around it.
- Updated `config.py`'s defaults to the new strings (no decision reversal — same provider, same cheap/strong split, same cost reasoning; just current model names). Re-estimate the $0.39/report figure once billing is on and the eval harness has real cost logs — 3.8 Flash/3.1 Pro pricing wasn't re-verified against this ADR's $0.50 cap in this pass.
