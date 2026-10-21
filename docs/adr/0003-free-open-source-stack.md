# 0003. Free / open-source stack, LLM key the only cost

- **Status:** Accepted
- **Date:** 2026-10-21
- **Amended by:** [0006](0006-rabbitmq-replaces-valkey.md) — RabbitMQ replaces Valkey in the stack.

## Context
Personal portfolio project paid out of pocket. Anyone cloning it should be able to run it with only an LLM key.

## Options considered
1. **Managed / paid services** (Tavily or Brave search, LangSmith, hosted Redis, SaaS tracing) — better quality and SLAs, but recurring cost and API keys for every user.
2. **Self-hosted open source** — SearXNG, Valkey, Postgres, Langfuse, Playwright, EDGAR, Wikidata, all in docker compose.

## Decision
Option 2. The LLM provider is the only paid dependency.

## Consequences
- Free to run and to clone.
- SearXNG depends on upstream engines with no SLA; the fetch layer sits behind an interface so a paid search API can be swapped in later.
- More containers to run locally.
- Optional: Ollama for extraction/verification to cut LLM cost further.
