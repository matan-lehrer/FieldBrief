# 🧭 FieldBrief

**Tell it a company name. Get back a cited strategy report, built on real frameworks — not vibes.**

FieldBrief sends a crew of AI agents out to research a company and its competitors, then runs the evidence through the same strategy frameworks an MBA would reach for: Five Forces, Business Model Canvas, VRIO, competitive positioning, SWOT, and a short list of recommendations. Every single claim in the report is a clickable link back to where it came from. If the agents couldn't find something, the report says so — out loud, not swept under the rug.

## 🤔 Why not just ask ChatGPT?

Because "ChatGPT, analyze Figma's competitive position" gives you something that *sounds* authoritative and is quietly making half of it up. FieldBrief is built around three rules instead:

- 🔍 **Framework-based, not one-shot.** The Five Forces, VRIO, and BMC sections aren't prose the model free-associated — they're built from a structured research pass, field by field, then assembled deterministically.
- 📎 **Every claim cites its source.** Hover any finding and you get a real link to the page it came from — a publisher, a date, nothing fabricated, nothing dressed up as a quote that isn't one.
- 🫥 **Honest about gaps.** Confidence per claim, "data as of" per section, and a dedicated "not found" list. An unknown is a perfectly fine answer. A made-up one is not.

## ⚙️ How it works

1. **Resolve** — you type a name, FieldBrief disambiguates it (so "Wix" doesn't quietly become a Swedish municipality) and goes hunting for real competitors.
2. **Plan** — a planner figures out what's still missing per research field.
3. **Research** — a swarm of researchers fetches it in parallel: the web, SEC filings, Wikidata — for the target company *and* every competitor, all at once.
4. **Verify** — every extracted claim gets checked against the actual source text before it's trusted.
5. **Synthesize** — one pass applies Five Forces, VRIO, BMC, and positioning, then rolls everything into a SWOT and a handful of prioritized recommendations, each traceable back to the claims behind it.
6. **Watch it happen** — progress streams live over SSE while it runs; the finished report is ready as HTML, PDF, or JSON.

## 🏗️ Architecture

FastAPI · LangGraph · Postgres · RabbitMQ (FastStream) · LiteLLM · SearXNG · Playwright — the full design rationale lives in [`docs/design.md`](docs/design.md) and the decision log in [`docs/adr/`](docs/adr/).

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

A cheap model handles extraction and verification; a stronger model is reserved for the one synthesis call per report — never called in a loop. Every report has a hard cost cap (target: **≤ $0.50** for a standard-tier report) and a time budget, so a flaky page or a stubborn gap can't run away with either.

## 🚀 Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Docker (enable Docker Desktop's WSL integration if you're on WSL2).

```bash
git clone <this repo> && cd FieldBrief
cp .env.example .env            # fill in LLM_API_KEY (a Gemini API key — see ADR 0008)
uv sync && uv run pre-commit install
docker compose up -d            # Postgres, RabbitMQ, SearXNG
uv run alembic upgrade head     # creates the tables
```

### 🖥️ From the CLI (fastest way to see it work)
```bash
uv run fieldbrief analyze Wix --tier standard
```
Resolves the company, researches it and its competitors, and prints the finished `report_json` to stdout. `--tier quick|standard|deep` and `--competitors N` (default: the tier's own count — 2/4/6) control scope and cost; `--competitors 0` for a single-company report.

### 🌐 Through the API (live progress, HTML/PDF export)
```bash
# one terminal:
uv run uvicorn fieldbrief.api.app:app --reload
# another terminal:
uv run faststream run fieldbrief.worker.app:app
```
Then open `http://localhost:8000` and type a company name — or drive it directly:
```bash
curl -s -X POST localhost:8000/companies/resolve \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" -d '{"query": "Wix"}'
# -> {"id": "...", "name": "Wix.com", ...}

curl -s -X POST localhost:8000/reports \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"company_id": "<id above>", "tier": "standard"}'
# -> {"report_id": "..."}
```
`http://localhost:8000/reports/<report_id>/view?api_key=<your key>` shows live progress while the report runs, then the finished report. `GET /reports/{id}/pdf` exports the same report as a PDF — same content, print-friendly layout, numbered footnotes instead of hover.

### ✅ Run the checks
```bash
make check   # lint + typecheck + test (needs docker compose up -d postgres)
```

## 📍 Status

Every stage in the [roadmap](docs/roadmap.md) is built, and the pipeline has been run for real — live against a real LLM, real SearXNG search, real SEC EDGAR filings, and real Wikidata lookups, not just mocked tests. Past the initial build, FieldBrief has gone through several rounds of hardening driven by what actually broke on real companies: evidence quality, budget/concurrency correctness, framework correctness, HTML/PDF content parity, and competitor-discovery reliability (a company's own homepage rarely names its own competitors — searching one page and giving up was the bug, not the company). See `docs/roadmap.md` for the full history.

Unit and DB-integration tests run with network/LLM calls mocked or hitting a real local Postgres — see `CLAUDE.md`'s testing rules for why. Quality beyond that is tracked by the eval harness below, not by the unit test suite.

## 📊 Evals

The golden-set harness (`evals/harness.py`) and 10 researched ground-truth companies (`evals/golden/*.yaml`) are built — see [ADR 0011](docs/adr/0011-eval-plan.md). Run `uv run python evals/harness.py` (needs `LLM_API_KEY` and the compose stack up) to generate `evals/results/<date>.md`.

---

Built by [Matan Lehrer](https://github.com/matan-lehrer) — backend/AI engineer with an MBA who got tired of competitive analysis decks that cite nothing.
