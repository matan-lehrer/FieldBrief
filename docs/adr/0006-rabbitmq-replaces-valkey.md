# 0006. RabbitMQ replaces Valkey + arq

- **Status:** Accepted
- **Date:** 2026-10-21
- **Supersedes:** the queue technology in [0002](0002-job-queue-not-kafka.md). Amends [0003](0003-free-open-source-stack.md) and [0005](0005-llm-gateway-and-budgets.md).

## Context
Valkey had three jobs: job queue (with arq), progress pub/sub, and shared rate-limit state. The research cache was already Postgres (fresh claims per company x field). Valkey is in-memory, so queued jobs can be lost on a crash unless persistence is tuned.

## Options considered
1. **Keep Valkey + arq** — one component, three jobs; weaker durability, no native priorities.
2. **RabbitMQ + Valkey** — durable queue, but four services to run.
3. **RabbitMQ only (drop Valkey)** — RabbitMQ for jobs and progress (topic exchange); Postgres for cache; rate limiting in-process (one worker) or Postgres-backed (several).

## Decision
Option 3. Stack is Postgres + RabbitMQ. Python client: FastStream (async, built on aio-pika).

## Design notes
- **Jobs:** durable queue `reports.jobs`, priority enabled (quick > standard > deep). Prefetch 1 per worker slot. Ack early — the LangGraph checkpoint in Postgres is the source of truth; the `Idempotency-Key` and report status prevent duplicate runs. Dead-letter queue for poison messages.
- **Progress:** topic exchange `reports.progress`, routing key `report.<id>`. Each SSE connection binds an exclusive auto-delete queue. On connect, the API first sends current status and finished sections from Postgres, then streams live events (late joiners miss nothing).
- **Recovery:** on worker start, reports stuck in `queued`/`running` past a timeout are re-enqueued.
- **Rate limits:** token bucket per provider in the gateway — in-process while there's one worker; Postgres-backed (or a cache layer) when there are several.

## Consequences
- Stronger delivery guarantees and priority lanes; one fewer component than option 2.
- New concepts to learn (exchanges, bindings, acks); SSE needs the bind-on-connect pattern above.
- Scale path: RabbitMQ cluster or SQS. Kafka remains reserved for event fan-out.
