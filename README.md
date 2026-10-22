# Fieldbrief

**AI-powered competitive analysis, built on real strategy frameworks.**

Fieldbrief turns a company name into a cited strategy report in minutes. AI agents research the company and its competitors, then apply proven frameworks (Five Forces, Business Model Canvas, VRIO, positioning) to derive a SWOT and strategic recommendations. Every claim links to its source.

> **Status:** Stages 0–8 of the [roadmap](docs/roadmap.md) are built and tested. The agent pipeline itself hasn't been run against a real LLM yet in this environment — see [Status in detail](#status-in-detail).

## Why it's different
- **Framework-based, not one-shot:** conclusions are built step by step from evidence through established strategy frameworks.
- **Cited and verified:** every claim is checked against its source; unsupported claims are flagged or dropped.
- **Honest about uncertainty:** confidence per claim, "data as of" per section, gaps shown, not hidden.
- **Cheap to run:** model tiering, a shared research cache, and per-report budgets. Fully open-source stack — the only cost is an LLM key.

## How it works
1. Enter a company; Fieldbrief resolves it via Wikidata (disambiguating e.g. a company from a place or surname that happens to share its name) and searches for real competitors.
2. A planner finds what's missing per field; researchers fetch it in parallel from the web, SEC filings, and the resolved company data — for the target and every competitor at once.
3. A verifier checks every extracted claim against the source snippet it came from.
4. One synthesis pass applies Five Forces, VRIO, BMC, and positioning, then rolls findings into a SWOT and 3–7 recommendations — each one linked to the claims behind it.
5. Watch progress live over SSE; read the report as HTML or export PDF / JSON.

## Architecture
FastAPI · LangGraph · Postgres · RabbitMQ (FastStream) · LiteLLM · SearXNG · Playwright — see [`docs/design.md`](docs/design.md) and the decision records in [`docs/adr/`](docs/adr/).

```
nginx -> FastAPI (resolve, reports, SSE) -> Postgres + RabbitMQ (jobs, progress)
                                                         |
                                     FastStream worker (one LangGraph per report)
                                     plan -> research -> verify -> gap_check -> analyze
                                        (per company, in parallel) -> synthesize -> deliver
                                                         |
                                        LLM gateway (LiteLLM) + fetch layer
                                        (SearXNG, httpx+trafilatura, SEC EDGAR, Wikidata)
```

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Docker (with Docker Desktop's WSL integration enabled, if you're on WSL2).

```bash
git clone <this repo> && cd FieldBrief
cp .env.example .env            # fill in LLM_API_KEY (a Gemini API key — see ADR 0008)
uv sync && uv run pre-commit install
docker compose up -d            # Postgres, RabbitMQ, SearXNG
uv run alembic upgrade head     # creates the 8 tables
```

### Run it from the CLI (simplest path)
```bash
uv run fieldbrief analyze Wix --tier standard
```
Resolves the company, researches it and its competitors, and prints the finished `report_json` to stdout. `--tier quick|standard|deep` and `--competitors N` (default: the tier's own count — 2/4/6) control scope and cost; `--competitors 0` for a single-company report.

### Run it through the API (live progress, HTML/PDF export)
```bash
# one terminal:
uv run uvicorn fieldbrief.api.app:app --reload
# another terminal:
uv run faststream run fieldbrief.worker.app:app
```
Then:
```bash
curl -s -X POST localhost:8000/companies/resolve \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" -d '{"query": "Wix"}'
# -> {"id": "...", "name": "Wix.com", ...}

curl -s -X POST localhost:8000/reports \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"company_id": "<id above>", "tier": "standard"}'
# -> {"report_id": "..."}
```
Open `http://localhost:8000/reports/<report_id>/view?api_key=<your key>` in a browser — it shows live progress while the report runs, then the finished report. `GET /reports/{id}/pdf` exports the same report as a PDF.

### Run the checks
```bash
make check   # lint + typecheck + test (needs docker compose up -d postgres)
```

## Status in detail
Every roadmap stage through 8 is built with real integration tests against the actual local Postgres/RabbitMQ stack (network and LLM calls mocked in tests — see `CLAUDE.md`'s testing rules for why). What that means concretely:

| Stage | What's built | Verified how |
|---|---|---|
| 0. Design | Full design doc, 12 ADRs | — |
| 1. Skeleton | docker compose, 8-table schema, CLI stub | `docker compose up`, migration round-trip, `fieldbrief --help` |
| 2. Data/LLM | Wikidata/EDGAR/SearXNG clients, LLM gateway | Live, against the real APIs |
| 3–4. Agent graph | Full pipeline: research, verify, Five Forces, VRIO, BMC, positioning, SWOT, recommendations | Real Postgres, mocked network/LLM |
| 5. Evals | Harness + 10 researched golden-set companies | Scoring logic unit-tested; not yet run live |
| 6. Service layer | FastAPI, RabbitMQ worker, SSE progress, idempotency | Full API->worker->SSE round-trip, real Postgres+RabbitMQ |
| 7. Report UI/PDF | Jinja2 report page, Playwright PDF, live-progress view | Render + API tests; **no screenshot** — this sandbox has no browser and Playwright's Chromium can't launch here (missing system libs, no root) |
| 8. Refresh | `POST /reports/{id}/refresh` | Full round-trip test |

**Not yet done, honestly:** this whole pipeline has not been run against a real LLM provider in this development session — there's no `LLM_API_KEY` configured in this environment. Every piece is tested with the network/LLM layers mocked. Running `fieldbrief analyze <company>` for real, once a key is set, is the next and most important verification step.

## Evals
The golden-set harness (`evals/harness.py`) and all 10 researched ground-truth files (`evals/golden/*.yaml`) are built — see [ADR 0011](docs/adr/0011-eval-plan.md). Results aren't published yet; run `uv run python evals/harness.py` (needs `LLM_API_KEY` and the compose stack up) to generate `evals/results/<date>.md`.

## License
MIT
