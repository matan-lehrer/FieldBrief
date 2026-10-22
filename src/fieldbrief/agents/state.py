"""LangGraph state for researching ONE company (ADR 0010's inner, per-field
fan-out level). The outer, per-company level is plain asyncio.gather over one
of these subgraphs per company (agents/orchestrator.py) — not a second
LangGraph layer; there's no conditional looping at that level, just parallel
dispatch and a join, so a state machine adds nothing there. Infra (DB session,
LLM gateway, budget, fetcher) is injected into node closures (agents/graph.py),
not carried in state — state is data, per ADR 0001.
"""

from typing import Any, TypedDict

from fieldbrief.agents.field_checklist import Tier


class Gap(TypedDict):
    field: str
    attempts: int


class GraphState(TypedDict):
    report_id: str
    company_id: str
    company_name: str
    company_domain: str | None
    company_cik: str | None  # set when Wikidata resolved a CIK — enables EDGAR filings as a source
    is_target: bool
    tier: Tier
    retry_round: int
    gaps: list[Gap]
    claim_ids: list[str]  # every claim_id produced for this company, for report_claims
    company_snapshot: dict[str, Any] | None  # built by analyze_node
