# Fieldbrief

**AI-powered competitive analysis, built on real strategy frameworks.**

Fieldbrief turns a company name into a cited strategy report in minutes. AI agents research the company and its competitors, then apply proven frameworks (Five Forces, Business Model Canvas, VRIO, positioning) to derive a SWOT and strategic recommendations. Every claim links to its source.

> **Status:** in design (Stage 0). See [`docs/roadmap.md`](docs/roadmap.md).

## Why it's different
- **Framework-based, not one-shot:** conclusions are built step by step from evidence through established strategy frameworks.
- **Cited and verified:** every claim is checked against its source; unsupported claims are flagged or dropped.
- **Honest about uncertainty:** confidence per claim, "data as of" per section, gaps shown, not hidden.
- **Cheap to run:** model tiering, a shared research cache, and per-report budgets. Fully open-source stack — the only cost is an LLM key.

## How it works
1. Enter a company; confirm it and its competitors.
2. A planner finds what's missing; researchers gather it in parallel from the web, SEC filings, and Wikidata.
3. A verifier checks every claim against its source.
4. The report builder applies the frameworks and writes a SWOT and recommendations, each linked to evidence.
5. Watch progress live; read the report on the web or export PDF / JSON.

## Architecture
FastAPI · LangGraph · Postgres · RabbitMQ · LiteLLM · SearXNG · Playwright · Langfuse — see [`docs/design.md`](docs/design.md) and the decision records in [`docs/adr/`](docs/adr/).

## Quickstart
_Coming in Stage 1._

## Evals
_Accuracy, citation validity, cost, and latency results will be published here (Stage 5)._

## License
MIT
