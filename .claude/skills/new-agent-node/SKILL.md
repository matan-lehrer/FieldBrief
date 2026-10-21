---
name: new-agent-node
description: Add or change a LangGraph node (planner, researcher, verifier, report builder, or a new one) in src/fieldbrief/agents. Use whenever writing or modifying agent graph code.
---

# New agent node

## Contract every node follows
- **Stateless function:** `async def node(state: GraphState) -> dict` returning only the state keys it updates.
- **Pass IDs, not payloads.** State holds report/company/claim IDs and small summaries; large data lives in Postgres and is loaded by ID.
- **LLM calls only through the gateway** (`fieldbrief.llm`): pass `task`, `messages`, the output `schema` (Pydantic), and `report_id`. Never import a provider SDK.
- **Right model tier:** extraction/verification use the cheap task types; only synthesis uses the strong one. No strong-model calls inside loops or fan-outs.
- **Untrusted input:** fetched page text goes into the prompt as clearly delimited data, and the output is a strict schema. Page text never selects tools or changes instructions.
- **Bounded:** any loop or retry has a cap and respects the report's cost and time budget. On budget exhaustion, return what you have and mark the gap — never raise to the user.
- **Progress:** emit a stage/progress event at the start and when there's a real finding (e.g. "Found 4 competitors").
- **Traced:** the gateway traces LLM calls; add a span for non-LLM work that can be slow (fetching).

## Steps
1. Define or extend the node's input/output fields in the graph state model.
2. Define the Pydantic output schema for any LLM call.
3. Write the node. Keep prompts in a separate module/file next to it, not inline strings scattered in logic.
4. Wire it in the graph: edges, conditional routing, fan-out (`Send`) if per-company or per-field.
5. Tests (no real LLM or network):
   - Happy path with a fake gateway returning a fixed schema object.
   - Budget exhausted -> graceful partial result.
   - Bad/empty source -> "not found" claim, no crash.
6. Run the relevant eval subset if the node affects report quality (see the `run-evals` skill).
7. If the node changes the graph shape, update the data flow in `docs/design.md`.
