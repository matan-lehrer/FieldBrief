---
name: design-check
description: Check a proposed change, feature, dependency, or new infrastructure against Fieldbrief's constraints, NFRs, and ADRs before building it. Use before adding a dependency or service, changing the data model, changing the agent graph, or whenever a change might conflict with docs/design.md.
---

# Design check

Answer each question for the proposed change. Report only the ones that fail or need attention, then give a verdict: **go**, **go with changes**, or **needs a decision from Matan**.

## Constraints (CLAUDE.md)
1. **Solo-buildable** — is this the simplest thing that meets the requirement? Is there a smaller version?
2. **Cost** — does it add LLM calls? Estimate tokens and $ per standard report. Does the total stay <= $0.50?
3. **Free stack** — does it add a paid service, SaaS, or a new API key? If yes: needs a decision.
4. **Scale seams** — does it close off the scale path (e.g. stores state in a worker, passes big payloads between nodes)?
5. **Accuracy** — can it produce an uncited or invented claim?

## NFRs
- Latency: does it add a serial step on the critical path? Can it run in parallel or be cached?
- Reliability: what happens if it fails mid-job? Does the report degrade to partial instead of failing?
- Observability: is it traced, with cost logged?

## Architecture
- Does it respect the ADRs in `docs/adr/`? If it contradicts one, a new ADR must supersede it.
- LLM calls through the gateway only? Outputs as Pydantic schemas?
- Claims append-only, reports immutable, scores derived?
- Untrusted web content kept as data (no prompt injection path)?

## Output
```
Verdict: go | go with changes | needs a decision
Issues:
- <issue> -> <suggested fix>
ADR needed: yes/no (why)
```
