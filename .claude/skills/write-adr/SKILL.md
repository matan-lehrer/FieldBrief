---
name: write-adr
description: Record an architecture decision in docs/adr. Use when a design choice is made or changed - a new dependency, data model change, new component, a trade-off between options, or reversing an existing ADR.
---

# Write an ADR

1. List `docs/adr/` and take the next number (`NNNN`, zero-padded).
2. Copy `docs/adr/0000-template.md` to `docs/adr/NNNN-short-kebab-title.md`.
3. Fill it in, briefly:
   - **Context** — the problem and which constraints/NFRs apply.
   - **Options considered** — at least two real options, each with honest pros and cons.
   - **Decision** — one or two sentences.
   - **Consequences** — what gets easier, what gets harder, and when we'd revisit.
4. If it replaces an earlier ADR: set the old one's status to `Superseded by NNNN` (don't change its decision text) and reference it from the new one.
5. Add a row to the table in `docs/adr/README.md`.
6. Update the matching summary line in `docs/design.md` section 8.

Keep it under a page. The point is the reasoning an interviewer would ask about: why this, why not the alternative.
