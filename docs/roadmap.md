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

## 7. Report UI and PDF  ← current
Jinja2 + HTMX pages, progressive sections, Playwright PDF.
**Done when:** README-ready screenshots.

## 8. Polish and ship
Tiers, refresh, partial-report handling, README with demo report, design doc + ADRs final, demo video.
**Done when:** a stranger can clone, run, and understand it in 10 minutes.
