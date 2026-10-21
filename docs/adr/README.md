# Architecture Decision Records

One file per significant decision: `NNNN-short-title.md`, numbered in order. Never edit an accepted ADR's decision — supersede it with a new one and link both ways.

Template: `0000-template.md`.

| # | Decision | Status |
|---|---|---|
| 0001 | Orchestrated agent graph, not choreographed services | Accepted |
| 0002 | Job queue, not Kafka | Accepted (queue tech superseded by 0006) |
| 0003 | Free / open-source stack, LLM key the only cost | Accepted |
| 0004 | Shared, append-only research as the cache | Accepted |
| 0005 | Single LLM gateway with model tiering and per-report budgets | Accepted (amended by 0006) |
| 0006 | RabbitMQ replaces Valkey + arq | Accepted |
| 0007 | Tagged claims for multi-dimension fields (Five Forces, VRIO) | Accepted |
