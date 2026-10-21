## What
<!-- One or two sentences. Link the roadmap stage. -->

## Why
<!-- The problem this solves. -->

## How to verify
<!-- Commands to run, expected output. -->

## Checklist
- [ ] `make check` passes
- [ ] Tests cover the change (no real LLM or network calls)
- [ ] LLM calls go through the gateway; outputs are Pydantic schemas
- [ ] Cost impact per standard report noted (if it adds LLM calls)
- [ ] Docs updated (`docs/design.md`, ADR, roadmap) if a decision or behavior changed
