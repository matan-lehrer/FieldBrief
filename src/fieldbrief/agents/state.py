"""LangGraph state for one report run (ADR 0010).

Stage 3 scope: single company, no competitors — the "outer, per-company"
level of ADR 0010's two-level fan-out collapses to one branch. Infra
(DB session, LLM gateway, budget, fetcher) is injected into node closures
(agents/graph.py), not carried in state — state is data, per ADR 0001.
"""

from typing import Any, TypedDict


class Gap(TypedDict):
    field: str
    attempts: int


class GraphState(TypedDict):
    report_id: str
    company_id: str
    company_name: str
    company_domain: str | None
    tier: str
    retry_round: int
    gaps: list[Gap]
    claim_ids: list[str]  # every claim_id produced this run, for report_claims
    company_snapshot: dict[str, Any] | None  # built by analyze_node, read by synthesize_node
    report_json: dict[str, Any] | None
