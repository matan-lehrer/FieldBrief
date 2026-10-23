from typing import Any, TypedDict

from fieldbrief.agents.field_checklist import Tier


class Gap(TypedDict):
    field: str
    attempts: int


class ExhaustedGap(Gap):
    reason: str


class GraphState(TypedDict):
    report_id: str
    company_id: str
    company_name: str
    company_domain: str | None
    company_cik: str | None
    is_target: bool
    tier: Tier
    retry_round: int
    gaps: list[Gap]
    exhausted_gaps: list[ExhaustedGap]
    claim_ids: list[str]
    company_snapshot: dict[str, Any] | None
