# 0005. Single LLM gateway with model tiering and per-report budgets

- **Status:** Accepted
- **Date:** 2026-10-21
- **Amended by:** [0006](0006-rabbitmq-replaces-valkey.md) — the shared rate limiter no longer lives in Valkey: in-process while there is one worker, Postgres-backed when there are several.

## Context
LLM calls are the main cost and the main bottleneck. NFRs: <= $0.50 per standard report, >= 99% job completion under provider rate limits.

## Options considered
1. **Agents call provider SDKs directly** — fast to start; cost, retries, and rate limits scattered everywhere.
2. **One gateway module every call goes through** — LiteLLM for provider abstraction, our own layer for task routing, budgets, and shared rate limits.

## Decision
Option 2. Callers pass a task and an output schema; the gateway picks the model (cheap for extraction/verification, strong for synthesis), reserves budget before the call and settles after, rate-limits per provider via Valkey (waits rather than fails), retries with backoff, validates output, and logs cost per report and stage.

## Consequences
- Cost cap becomes a guarantee; parallel researchers can't jointly overspend (reserve-then-settle).
- Swapping models or providers is configuration.
- One more layer to build; LiteLLM covers the commodity part.
