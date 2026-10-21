---
name: stage-kickoff
description: Start or resume a roadmap stage or stage item. Use when the user says "start stage N", "next step", "what's next", or picks an item from docs/roadmap.md.
---

# Stage kickoff

1. Read `docs/roadmap.md` and find the current stage (marked `← current`) or the one the user named.
2. Read the parts of `docs/design.md`, `docs/adr/`, and `docs/open-questions.md` that the stage touches.
3. Check blockers: if the stage depends on an unanswered item in `docs/open-questions.md`, say so and stop — propose how to resolve it instead of coding around it.
4. Write a short plan for the user, and wait for approval before coding:
   - **Goal** — one sentence, from the stage's "Done when"
   - **Steps** — 3–8 small, committable steps
   - **Files** — what gets created or changed
   - **Tests** — what proves each step works
   - **Risks / decisions** — anything that might need an ADR
5. Work step by step. After each step: run `make check`, commit with a conventional message.
6. At the end: verify the stage's "Done when" literally (run the command it names), tick the boxes in `docs/roadmap.md`, move the `← current` marker, and update `CLAUDE.md`'s "Current stage" line.

For design items in Stage 0, the output is a written decision in `docs/design.md` (plus an ADR if significant), not code.
