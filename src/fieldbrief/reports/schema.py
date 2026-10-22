"""Pydantic models for `report_json` (REPORTS.report_json, design.md §5).

This is the contract between synthesis (agents/) and everything that reads a
report (API, CLI, UI, PDF). Keep this in lockstep with design.md §5-7 and
ADR 0007/0009/0010 — update both together.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class Tier(StrEnum):
    quick = "quick"
    standard = "standard"
    deep = "deep"


class Freshness(BaseModel):
    """'Data as of' + staleness for one section, per field-checklist.md freshness rules."""

    as_of: date
    stale: bool


class Confidence(BaseModel):
    """Derived at build time (design.md §3), frozen into the report — never recomputed after."""

    score: float = Field(ge=0, le=1)
    corroboration: int  # independent sources agreeing
    verified: bool
    # A reader-facing evidence status derived from the same inputs as
    # `score` — meant to replace showing `score` as a bare percentage
    # (easy to misread as "% likely true"). Default exists only so a
    # report_json saved before this field existed still renders.
    status: Literal["strong", "supported", "unverified", "conflicting"] = "supported"


class Citation(BaseModel):
    """One field's answer for one company: the claims behind it plus their rolled-up confidence."""

    claim_ids: list[UUID]
    confidence: Confidence


class CompanySnapshot(BaseModel):
    """profile.* / offering.* / financials.* for one company. recent_events is
    target-only (field-checklist.md)."""

    company_id: UUID
    name: str
    is_target: bool
    description: Citation | None = None
    hq: str | None = None
    founded: int | None = None
    employees: Citation | None = None
    products: Citation | None = None
    customer_segments: Citation | None = None
    revenue_model: Citation | None = None
    revenue: Citation | None = None
    profitability: Citation | None = None
    funding_or_cap: Citation | None = None
    recent_events: Citation | None = None
    freshness: Freshness


class ForceAssessment(BaseModel):
    """One Five Forces dimension. claim_ids come from claims tagged
    value.force == force (ADR 0007)."""

    force: Literal["buyer_power", "supplier_power", "substitutes", "entry_barriers", "rivalry"]
    rating: Literal["low", "medium", "high"]
    rationale: str
    claim_ids: list[UUID]


class FiveForces(BaseModel):
    forces: list[ForceAssessment] = Field(default_factory=list)
    freshness: Freshness | None = None


class BMCBlock(BaseModel):
    block: Literal[
        "segments",
        "value_propositions",
        "channels",
        "customer_relationships",
        "revenue_streams",
        "key_resources",
        "key_activities",
        "key_partners",
        "cost_structure",
    ]
    statement: str
    claim_ids: list[UUID]


class BusinessModelCanvas(BaseModel):
    blocks: list[BMCBlock] = Field(default_factory=list)
    freshness: Freshness | None = None


class VRIOResource(BaseModel):
    """One resource/advantage. claim_ids come from claims tagged
    value.resource == resource (ADR 0007)."""

    resource: str
    valuable: bool
    rare: bool
    inimitable: bool
    organized: bool
    competitive_implication: Literal[
        "competitive_disadvantage", "parity", "temporary_advantage", "sustained_advantage"
    ]
    rationale: str
    claim_ids: list[UUID]


class PositioningAxis(BaseModel):
    name: str
    low_label: str
    high_label: str
    rationale: str


class PositionedCompany(BaseModel):
    company_id: UUID
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    claim_ids: list[UUID]


class PositioningMap(BaseModel):
    x_axis: PositioningAxis | None = None
    y_axis: PositioningAxis | None = None
    companies: list[PositionedCompany] = Field(default_factory=list)


class SWOTItem(BaseModel):
    statement: str
    derived_from: Literal[
        "five_forces", "vrio", "events", "bmc", "market_size", "regulation", "other"
    ]
    claim_ids: list[UUID]


class SWOT(BaseModel):
    strengths: list[SWOTItem] = Field(default_factory=list)
    weaknesses: list[SWOTItem] = Field(default_factory=list)
    opportunities: list[SWOTItem] = Field(default_factory=list)
    threats: list[SWOTItem] = Field(default_factory=list)


class Recommendation(BaseModel):
    statement: str
    rationale: str
    priority: Literal["high", "medium", "low"]
    swot_refs: list[int] = Field(
        description="indices into the SWOT list(s) this recommendation addresses"
    )
    claim_ids: list[UUID]


class CoverageGap(BaseModel):
    """An explicit 'not found' or budget cutoff (ADR 0010), per field-checklist.md
    rule 1 — never a silently missing section."""

    company_id: UUID
    field: str
    attempts: int
    reason: Literal["not_found", "budget_cutoff"] = "not_found"


class ReportMeta(BaseModel):
    target_company_id: UUID
    competitor_ids: list[UUID] = Field(default_factory=list)
    tier: Tier
    generated_at: datetime
    data_as_of: date


class ReportJSON(BaseModel):
    meta: ReportMeta
    companies: list[CompanySnapshot] = Field(default_factory=list)
    five_forces: FiveForces = Field(default_factory=FiveForces)
    bmc: BusinessModelCanvas = Field(default_factory=BusinessModelCanvas)
    vrio: list[VRIOResource] = Field(default_factory=list)
    positioning: PositioningMap = Field(default_factory=PositioningMap)
    swot: SWOT = Field(default_factory=SWOT)
    recommendations: list[Recommendation] = Field(default_factory=list)
    gaps: list[CoverageGap] = Field(default_factory=list)
