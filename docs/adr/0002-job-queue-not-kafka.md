# 0002. Job queue, not Kafka

- **Status:** Accepted. Queue technology superseded by [0006](0006-rabbitmq-replaces-valkey.md) (RabbitMQ instead of Valkey + arq); "queue, not Kafka" still holds.
- **Date:** 2026-10-21

## Context
The API must acknowledge in <= 200 ms while reports take minutes. Work must be decoupled: one worker per job, retry if a worker dies.

## Options considered
1. **Kafka** — durable ordered log, great for event fan-out and replay. As a job queue: parallelism capped by partitions, offsets commit in order (a slow 10-minute job blocks its partition), no per-message ack or visibility timeout, heavy to operate.
2. **Task queue (Valkey + arq now, SQS at scale)** — per-job ack, retries, visibility timeout, dead-letter; trivial to run.

## Decision
Task queue: Valkey + arq in the MVP, SQS on the scale path.

## Consequences
- Matches the workload: discrete, long-running jobs.
- Revisit if we add event fan-out (e.g. `report.completed` feeding analytics, alerts, search indexing) — that's Kafka's strength.
