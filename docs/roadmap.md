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

## 1. Skeleton  ← current
Repo, docker compose (Postgres, RabbitMQ, SearXNG; Langfuse later), Alembic migrations for the 8 tables, CI (lint + typecheck + tests), CLI stub.
**Done when:** `docker compose up` runs and `fieldbrief --help` works.

## 2. Data and LLM access
Fetch layer (SearXNG search, httpx + trafilatura, URL/hash dedup), EDGAR client, Wikidata resolution, LLM gateway (routing, budget, cost logging).
**Done when:** the CLI resolves "Wix" and fetches a clean source.

## 3. One company, end to end (thin slice)
Planner -> researchers -> verifier for a single company; claims + sources saved; minimal report (profile + SWOT with citations).
**Done when:** `fieldbrief analyze Wix --competitors 0` produces a cited JSON report.

## 4. Competitors and frameworks
Competitor discovery + graph, parallel fan-out, cache reuse, Five Forces, BMC, VRIO, positioning, SWOT + recommendations linked to claims.
**Done when:** a full standard report runs from the CLI.

## 5. Evals
Golden-set harness: claim precision, citation validity, cost, latency. Tune to the NFRs.
**Done when:** an eval results table is committed.

## 6. Service layer
FastAPI endpoints, RabbitMQ queue + FastStream worker, LangGraph checkpointing, SSE progress, nginx, idempotency keys.
**Done when:** the same report runs through the API with live progress.

## 7. Report UI and PDF
Jinja2 + HTMX pages, progressive sections, Playwright PDF.
**Done when:** README-ready screenshots.

## 8. Polish and ship
Tiers, refresh, partial-report handling, README with demo report, design doc + ADRs final, demo video.
**Done when:** a stranger can clone, run, and understand it in 10 minutes.
