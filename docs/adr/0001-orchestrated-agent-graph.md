# 0001. Orchestrated agent graph, not choreographed services

- **Status:** Accepted
- **Date:** 2026-10-21

## Context
The pipeline has planner, researcher, verifier, and report-builder stages, with a stateful loop (verify -> replan -> research gaps). We want modular, stateless, horizontally scalable components, built by one person.

## Options considered
1. **Service per stage, connected by Kafka (choreography)** — independent scaling and isolation per stage. But every stage waits on the same LLM APIs, so separate scaling buys little; the retry loop becomes a distributed state machine (correlation IDs, sagas, timeouts, duplicates); more hops, harder tracing; much more to build.
2. **One LangGraph per report, run by interchangeable workers (orchestration)** — nodes are stateless functions over checkpointed state; any worker can run or resume any job; research fans out in parallel inside the graph.

## Decision
Option 2. One orchestrated graph per report, stateless nodes, state checkpointed in Postgres. Nodes pass IDs, not payloads, so any node can be extracted into a service later.

## Consequences
- Simple to build, reason about, trace, and test.
- Scaling = more workers. The real limit is LLM provider capacity, handled in the gateway.
- Extract a stage into its own service only when its resource profile differs (fetch layer with browsers, PDF rendering).
- At large scale, run the graph inside a durable-execution engine (Temporal) rather than switching to choreography.
