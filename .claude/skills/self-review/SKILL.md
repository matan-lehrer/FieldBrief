---
name: self-review
description: Senior-level review of the current changes before committing or opening a PR. Use when the user asks to review, before a commit that finishes a step, or when asked "is this ready?".
---

# Self-review

Review `git diff` (staged + unstaged) against `main`. Report only real findings, most severe first, each with file:line and a concrete fix. Then a one-line verdict.

## Correctness
- Does it do what the step's "Done when" says? Run it.
- Edge cases: empty/unknown data, unresolved company, source fetch failure, budget exhausted, duplicate job.
- Async: no blocking calls in async code, no unawaited coroutines, timeouts on all network calls.

## Design rules (CLAUDE.md)
- LLM calls only via the gateway; outputs validated by Pydantic schemas; right model tier.
- Claims append-only, reports immutable, scores derived.
- Nodes stateless, pass IDs not payloads; loops capped.
- No new paid service or dependency without a decision.

## Security
- Fetched content treated as untrusted data (prompt injection).
- No secrets in code, logs, or fixtures. Settings from env via pydantic-settings.
- SQL via SQLAlchemy only, no string-built queries.
- User identity from auth, never from the request body.

## Quality
- Types complete (mypy strict passes), names clear, no dead code or speculative abstraction.
- Tests cover the change and don't touch the network or real LLMs.
- `make check` passes.
- Docs updated if behavior or a decision changed (`design.md`, ADR, roadmap).

## Output
```
Verdict: ready | ready after fixes | not ready
1. [severity] file:line — problem -> fix
```
