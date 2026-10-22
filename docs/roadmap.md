# Roadmap to MVP

Each stage ends with something that runs. Update the checkboxes as items finish.

**Why this order:** the CLI path (stages 1–5) proves the core value before any API or UI work. A CLI that produces great, eval-backed reports is already a strong portfolio piece.

## 0. Finish the design
- [x] Requirements, NFRs, BOTE, entities, tables, API, data flow, architecture
- [x] Field checklist — approve the DRAFT (`field-checklist.md`)
- [x] Report schema — Pydantic models for `report_json`
- [x] Framework method — inputs, scoring, roll-up into SWOT, positioning axes
- [x] Agent graph — nodes, state, edges, fan-out, stop conditions, partial reports
- [x] Eval plan — golden set (~10 companies), metric definitions per NFR
- [x] Model picks — cheap + strong models, prices verified

**Done when:** the whole flow can be explained input -> report with no TBDs.

## 1. Skeleton
Repo, docker compose (Postgres, RabbitMQ, SearXNG; Langfuse later), Alembic migrations for the 8 tables, CI (lint + typecheck + tests), CLI stub.
**Done when:** `docker compose up` runs and `fieldbrief --help` works. ✅ Verified 2026-10-22: all 3 services healthy, migration applies and rolls back cleanly against real Postgres, `fieldbrief --help`/`version` work. Postgres host port is 5433 (5432 is often already taken by a system install).

## 2. Data and LLM access
Fetch layer (SearXNG search, httpx + trafilatura, URL/hash dedup), EDGAR client, Wikidata resolution, LLM gateway (routing, budget, cost logging).
**Done when:** the CLI resolves "Wix" and fetches a clean source. ✅ Verified 2026-10-22: `fieldbrief resolve Wix` live — correctly disambiguates the company from the Essex village/surname (ADR 0011's exact test case), fetches and cleans wix.com. EDGAR (ticker->CIK->filings) and LLM gateway (routing, budget, rate limit, schema validation+repair, cost settlement via LiteLLM) built and tested (36 unit tests, 4 live integration tests).

## 3. One company, end to end (thin slice)
Planner -> researchers -> verifier for a single company; claims + sources saved; minimal report (profile + SWOT with citations).
**Done when:** `fieldbrief analyze Wix --competitors 0` produces a cited JSON report. Built and tested (agent graph: plan/research/verify/gap_check/analyze/synthesize/deliver, all 50 tests passing, full graph run verified against real Postgres with network/LLM mocked) — not yet run against a real LLM (needs `LLM_API_KEY`).

## 4. Competitors and frameworks
Competitor discovery + graph, parallel fan-out, cache reuse, Five Forces, BMC, VRIO, positioning, SWOT + recommendations linked to claims.
**Done when:** a full standard report runs from the CLI. Built and tested (ADR 0012 competitor discovery; field_checklist.py as code; one consolidated synthesis call for Five Forces/VRIO/positioning/recommendations per ADR 0008's cost model; deterministic SWOT roll-up per ADR 0009; `agents/orchestrator.py`'s two-level fan-out — parallel per-company research, cross-company join; 55 tests passing, full standard-tier + competitor scenario verified against real Postgres with network/LLM mocked) — not yet run against a real LLM (needs `LLM_API_KEY`).

## 5. Evals
Golden-set harness: claim precision, citation validity, cost, latency. Tune to the NFRs.
**Done when:** an eval results table is committed. Harness built (`evals/harness.py`, 6 unit tests) and all 10 golden-set files researched and committed (`evals/golden/*.yaml`, ADR 0011) — not yet run for real (needs `LLM_API_KEY`); no results table committed yet.

## 6. Service layer
FastAPI endpoints, RabbitMQ queue + FastStream worker, LangGraph checkpointing, SSE progress, nginx, idempotency keys.
**Done when:** the same report runs through the API with live progress. ✅ Verified 2026-10-22 end to end against real Postgres + real RabbitMQ (network/LLM mocked): `POST /companies/resolve` -> `POST /reports` (enqueues, worker picks it up) -> `GET /reports/{id}/events` SSE stream reaches a terminal state -> `GET /reports/{id}` shows the finished report. Idempotency keys verified (same key -> same report, no duplicate). Found and fixed a real race during testing: a topic-exchange publish-before-subscribe can drop a progress event for good — `events` now falls back to the `Report` row's own status so a client can't hang (design.md §8 implementation notes). nginx config written but not yet wired into compose (no Dockerfile for the API/worker yet — they run via `uv run`, not as containers); LangGraph Postgres checkpointing (worker-crash resumability) deliberately deferred, not silently dropped — not needed for a single-worker MVP and no crash-recovery test exists yet to prove it.

## 7. Report UI and PDF
Jinja2 + HTMX pages, progressive sections, Playwright PDF.
**Done when:** README-ready screenshots. Built and tested: `reports/render.py` + `reports/templates/report.html` (company snapshots, Five Forces, BMC, VRIO, positioning scatter, SWOT, recommendations, gaps, all with resolved citations), `GET /reports/{id}/view` (finished report, or a live SSE progress shell while running), `GET /reports/{id}/pdf` (Playwright). **Literal done-when not yet met:** no screenshot exists. Rendering is verified correct (9 tests: Jinja2 output, citation resolution, the full API pipeline's `/view` response) but there's no browser in this sandbox to open the page and capture one, and Playwright's Chromium can't launch here either (missing system shared libraries, no root to install them — `playwright install --with-deps` needs sudo). Take the actual screenshot by opening a real report's `/view` URL in your own browser once you've run a real report.

## 8. Polish and ship
Tiers, refresh, partial-report handling, README with demo report, design doc + ADRs final, demo video.
**Done when:** a stranger can clone, run, and understand it in 10 minutes. Tiers (quick/standard/deep, `--competitors N`) and partial-report handling (`status="partial"` on a budget cutoff, `agents/orchestrator.py`) were already built in stages 3–4. This stage added `POST /reports/{id}/refresh` (new immutable report linked via `parent_report_id`, full round-trip test) and a rewritten README (quickstart, CLI/API usage, status table) — every command in it live-verified against the real local stack, including the exact curl examples. Design doc and all 12 ADRs reviewed for consistency with the final build; no reversed decisions found. **Not done:** no screenshot/demo video (needs a real browser and a real `LLM_API_KEY`, neither available in this sandbox) — the one true gap left before this is a fully demonstrated MVP.

## 9. Self-serve demo UI  ← current
Not in the original plan — added after running the first real report live (stage 8) surfaced that there was no way to *use* FieldBrief without curl, and that a failed report's progress page looped forever (`location.reload()` on a terminal "failed" stage reopened the same SSE shell, which immediately got "failed" again via the DB catch-up fallback, and reloaded again — hit live by a real user against a real Gemini billing error).
**Done when:** a stranger can open the site, type a company name, and watch a real analysis run without touching curl or a terminal. Built:
- `GET /` — a landing page (`reports/templates/home.html`) with a company-name field and a tier picker; its own JS drives `/companies/resolve` -> `POST /reports` -> redirects to `/view`. Entirely new: previously the two POST endpoints were API-only.
- A real step indicator on the progress page (`templates/progress.html`) — Discover / Research / Analyze / Synthesize, driven by the same `Stage` enum the worker already emitted (`agents/progress.py`), replacing a single line of status text.
- **The actual bug fix:** `/view` now checks `report.status == "failed"` and renders a stable terminal page (`templates/failed.html`, with a "Try again" button wired to `POST /reports/{id}/refresh`) instead of falling through to the shell, which only ever had a landing spot for `done`/`partial`. Regression test: `test_view_shows_failed_page_not_an_infinite_shell_loop`.
- A shared stylesheet (`static/style.css`, self-hosted, no external fonts/CDN per ADR 0003) used by all four pages, and a visual pass on the finished report template (section icons, refined cards/badges/positioning chart) so the whole product reads as one thing.
- Verified without spending any LLM tokens: a hand-built `report_json` (no LLM calls) inserted directly as a `status="done"` row to exercise the real rendering path end-to-end, plus `running`/`failed` rows for the other two page states — all four pages confirmed rendering with no template errors via the real running server before being cleaned up.

## Reliability pass (v0.2.0, post-stage-9)
A real run surfaced that "not found" was often wrong, not true — three dropped/missing wires, not design gaps, found by reading the code rather than re-running real LLM calls:
- `profile.hq_founded` and `profile.employees` were extracted and verified like any other claim but `analyze_node` never read either into the snapshot — `report.html`'s `{% if c.hq %}` markup always rendered against `None`. Fixed, plus industry/ticker (already fetched from Wikidata onto `Company`, previously unused) now shown on the company card.
- `fetch/edgar.py` (SEC EDGAR: ticker→CIK→10-K/20-F/40-F) existed and was tested but was never called — financials for public companies (a CIK is known) now try the official filing first (`source_type="filing"`, `reports/confidence.py`'s top reliability tier) before falling back to scraped search results.
- Corroboration (`reports/confidence.py`'s bonus for multiple independent sources) was effectively dead code: the research loop fetched exactly one URL per field group per retry round, and a retry round only fires while the gap stays open — one successful source always closed it before a second was ever tried. Financials and `market.forces` fields now fetch two independent search results in the same round.
- Web report gained a High/Medium/Low trust badge per cited field (same underlying `Confidence.score`, now visible at a glance).

## History page and logo (v0.3.0)
- `GET /history` — the single MVP user's past reports, newest first (`Report.user_id` + the existing `ix_reports_user_created` index, zero new tables), each linking to `/view` and, when finished, `/pdf`. Linked from the home page and the finished-report topbar.
- A small inline-SVG compass mark (no external file, no CDN — ADR 0003) next to the "FieldBrief" wordmark on every page, including the PDF export.

## Report page UX overhaul + a real SWOT gap (v0.4.0)
Every real report run so far had `vrio: []` — confirmed across all 5 finished reports in the DB. `agents/synthesis.py`'s ADR-0009 roll-up derives Strengths/Weaknesses *only* from VRIO, and VRIO only from `resources.advantages` claims, which shared one search query/page with `market.forces` in `agents/nodes.py`'s `SEARCH_GROUPS` — a page about industry dynamics but no named resource left VRIO (and S/W) empty every time. Split into two independent search groups.
- `report.html`: sources are now a hover/click/focus popover trigger (`N sources`) instead of an always-visible list under every single cited item — the biggest single source of page clutter. A sticky in-page nav (only linking to sections the report actually has) with scroll-spy highlighting. Scroll-triggered reveal animation on every card/section (respects `prefers-reduced-motion`). The positioning chart's points animate in with a stagger when it scrolls into view.
- `pdf_report.html` gained a one-line company description under the title (resolved via the same claims-map pattern as the web report) — previously zero context on what the company *is* before the frameworks; still re-verified at 1 page against a real report.

## Evidence quality pass (v0.5.0)
First stage of a larger reliability/correctness/UX overhaul (`docs/adr/` gets 0013+ as later stages land); this stage is the evidence pipeline only, since every later stage builds on it:
- **Cross-company dedup bug**: `get_or_create_source`'s `content_hash`-changed flag is global to a Source row, not scoped to a company. A second company (or a new field) hitting a URL some other company/field had already consumed got silently skipped. Fixed with `get_covered_fields_from_source` (`db/repositories.py`) — only skip re-extraction when *this* company already has a *fresh* claim for *every* field the fetch is targeting, from this exact source.
- **Passages, not whole pages**: `Claim.snippet` stored the entire cleaned page for every claim extracted from it. The extractor now also returns a verbatim `excerpt` per claim; it's only kept if a cheap, deterministic substring check confirms it's genuinely in the page (never a fabricated "source quotation") — otherwise the claim keeps an empty snippet and relies on the separate verification call (still checked against the full page) for its `verified` flag.
- **`page_text[:8000]`**: extraction (and competitor discovery) used to blindly slice the head of the page — a claim relevant to a requested field three screens down never reached the model. Replaced with `select_relevant_passages` (`fetch/clean.py`): bounded, keyword-relevance-ranked paragraph selection, no new LLM call.
- **Title/publish date**: `Source.title`/`published_at` were accepted by `get_or_create_source` but no call site ever passed them — every source had `title=None`. Now filled from the search result's own title (or `fetch/clean.py`'s new `extract_metadata`, a local trafilatura metadata pass) at every call site.
- **Source classification**: `_source_type` only split official-domain vs. everything-else-"news". Added `.gov` → filing, known press-wire domains → official, known blog platforms → blog.
- **Freshness, for real**: `analyze_node`'s snapshot query loaded every verified claim ever stored, with no age filter, and `Freshness.stale` was hardcoded `False`. Now filtered to each field's real freshness window (the same one `gap_check` already uses) and `stale` is computed from whether any field's only evidence aged out.
- **Sub-dimension coverage**: `market.forces`/`resources.advantages` (ADR 0007's tagged multi-dimension fields) used to close on a single claim. `plan_node`/`gap_check_node` now share a `_field_resolved` check requiring several distinct `value.force`/`value.resource` tags before treating the field as done, still bounded by the existing per-tier round cap.
- `reports/confidence.py` gained conflict detection (disagreeing numeric values between sources no longer get a corroboration bonus) and a reader-facing `Confidence.status` (`strong | supported | unverified | conflicting`) alongside the existing numeric score — rendering it in HTML/PDF is a later stage.

## Budget and concurrency correctness (v0.6.0)
Stage 2 of the reliability/correctness/UX overhaul — the LLM gateway's cost/time guardrails, found to be racy, partially unenforced, or silently crash-prone rather than actually degrade a report to `partial`:
- `llm/budget.py`'s `Budget.check()`/`.settle()` straddled an `await litellm.acompletion(...)` with no lock, shared across `agents/orchestrator.py`'s `asyncio.gather`'d per-company research — two concurrent calls could both pass check before either settled, overshooting the budget. Replaced with `reserve()` (atomically check-and-commit an estimate, no `await` in between) + `settle(actual - estimated)` to true up; `check()` stays as a pure, non-mutating look.
- `llm/gateway.py`'s repair-retry call (`_repair`) was never budget-checked or settled — a second real provider call, effectively free and uncapped. It now reserves/settles through the same `Budget`.
- Tier budgets existed only as a flat `report_budget_usd_standard` setting; the CLI applied an ad hoc ×0.2/×1/×4 multiplier, but the worker (the real API path) and the eval harness both ignored tier entirely. Centralized as `agents/field_checklist.py`'s `budget_usd_for`/`time_budget_seconds_for`, used identically by the worker, CLI, and eval harness.
- `BudgetExceededError` was never caught anywhere — it propagated out of `asyncio.gather` to the worker's generic `except Exception`, marking the whole report `"failed"`. `agents/orchestrator.py` now catches it per company (degrading to a snapshot built from whatever claims already landed before the cutoff) and around competitor discovery (falls back to target-only) — a real budget cutoff now produces a `partial` report, never a crash.
- `report_time_budget_seconds` was defined and never read anywhere. `should_continue_researching` now checks an actual deadline (tier-scaled, same mechanism as the cost budget).
- `MAX_ATTEMPTS_PER_FIELD` (field-checklist.md rule 1: "after N attempts, an explicit unknown") was defined but never enforced — only the tier's round cap stopped retries, which could let deep tier (3 rounds) retry a single field past its own 2-attempt cap. `gap_check_node` now drops a field once it hits the cap (tracked in a new `exhausted_gaps` state key so it still surfaces as a real `CoverageGap` instead of vanishing).
- Fixed the `CoverageGap.reason` mislabeling this surfaced: it was driven by attempt count alone, which mislabeled ordinary round-cap exhaustion (e.g. every quick-tier gap, which can never reach 2 attempts in 1 round) as `"budget_cutoff"`. Now `"budget_cutoff"` means an actual $ or time cutoff ended research early; everything else that tried its allotted attempts/rounds is an honest `"not_found"`.

## Framework correctness (v0.7.0)
Stage 3 of the reliability/correctness/UX overhaul — a deeper pass over `agents/synthesis.py`/`llm_schemas.py`/`reports/schema.py` found real correctness gaps beyond rendering, documented in full in `docs/adr/0013-framework-correctness-fixes.md` (supersedes one clause of ADR 0009):
- `entry_barriers` renamed to `threat_of_new_entrants` — nothing (field name, prompt, or schema) told the model which direction "high" meant for this one force, unlike every other force where "high pressure" and the field name agree.
- VRIO's `organized` flag was deliberately decorative (ADR 0009's original choice) — now a 5th `competitive_implication` value, `unexploited_advantage` (valuable+rare+inimitable+not organized), rolls into Strengths while keeping the "not capturing it" gap visible for a recommendation to address.
- Positioning was the one roll-up loop in `synthesis.py` with no evidence check — a company could be charted with zero supporting claims. Now requires real evidence per placement and at least 2 placed companies before showing a map at all.
- Added `"insufficient_evidence"` as a 4th Five Forces rating (omitted from SWOT, shown honestly in Five Forces) instead of forcing a directional guess on thin evidence.
- Strengths/Weaknesses came only from VRIO; `other_strengths`/`other_weaknesses` now mirror the `other_opportunities`/`other_threats` free-judgment path for an internal capability signal (financials, disclosed risks) that isn't a `resources.advantages` claim.
- Tightened the extraction prompt: a bare margin/profitability number alone is no longer accepted as BMC `cost_structure` evidence.
- Found and fixed a real pre-existing test bug while verifying this against the full mocked report-generation test: the positioning mock supplied empty `source_claim_indices`, which the new evidence-gate schema correctly rejects — fixed the fixture, not the gate.
