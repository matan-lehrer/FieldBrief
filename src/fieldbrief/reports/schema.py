from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class Tier(StrEnum):
    quick = "quick"
    standard = "standard"
    deep = "deep"


Audience = Literal["analyst", "investor", "leadership", "competitor", "sales_partnership"]
DEFAULT_AUDIENCE: Audience = "analyst"


class Freshness(BaseModel):
    as_of: date
    stale: bool


class Confidence(BaseModel):
    score: float = Field(ge=0, le=1)
    corroboration: int
    verified: bool
    status: Literal["strong", "supported", "unverified", "conflicting", "different_scope"] = (
        "supported"
    )


class Citation(BaseModel):
    claim_ids: list[UUID]
    confidence: Confidence


class CompanySnapshot(BaseModel):
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
    funding: Citation | None = None
    market_cap_or_valuation: Citation | None = None
    recent_events: Citation | None = None
    freshness: Freshness


class ForceAssessment(BaseModel):
    force: Literal[
        "buyer_power", "supplier_power", "substitutes", "threat_of_new_entrants", "rivalry"
    ]
    rating: Literal["low", "medium", "high", "insufficient_evidence"]
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
    resource: str
    valuable: bool
    rare: bool
    inimitable: bool
    organized: bool
    competitive_implication: Literal[
        "competitive_disadvantage",
        "parity",
        "temporary_advantage",
        "unexploited_advantage",
        "sustained_advantage",
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
        "five_forces",
        "vrio",
        "events",
        "bmc",
        "market_size",
        "regulation",
        "financials",
        "risks",
        "other",
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
    action: str
    expected_benefit: str
    key_assumption: str
    next_validation_step: str
    priority: Literal["high", "medium", "low"]
    swot_refs: list[int] = Field(
        description="indices into the SWOT list(s) this recommendation addresses"
    )
    claim_ids: list[UUID]


class CompanyIntroduction(BaseModel):
    company_id: UUID
    intro: str
    claim_ids: list[UUID]


class CoverageGap(BaseModel):
    company_id: UUID
    field: str
    attempts: int
    reason: Literal["not_found", "partial", "budget_cutoff"] = "not_found"


class OverreachFlag(BaseModel):
    section: Literal["executive_brief", "recommendations"]
    statement: str


class ResearchSummary(BaseModel):
    source_type_counts: dict[str, int] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class ReportMeta(BaseModel):
    target_company_id: UUID
    competitor_ids: list[UUID] = Field(default_factory=list)
    tier: Tier
    audience: Audience = DEFAULT_AUDIENCE
    generated_at: datetime
    data_as_of: date
    synthesis_budget_cutoff: bool = False
    overreach_flags: list[OverreachFlag] = Field(default_factory=list)
    competitor_coverage: Literal["full", "partial", "none", "not_requested"] = "not_requested"
    research_summary: ResearchSummary = Field(default_factory=ResearchSummary)


class ExecutiveFinding(BaseModel):
    statement: str
    why_it_matters: str
    claim_ids: list[UUID]


class ComparisonCell(BaseModel):
    company_id: UUID
    citation: Citation | None = None


class ComparisonRow(BaseModel):
    dimension: str
    label: str
    cells: list[ComparisonCell]


class ComparisonTable(BaseModel):
    rows: list[ComparisonRow] = Field(default_factory=list)


class AudienceImplications(BaseModel):
    audience: Audience
    relevance: str
    assumptions: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    claim_ids: list[UUID] = Field(default_factory=list)


class ReportJSON(BaseModel):
    meta: ReportMeta
    companies: list[CompanySnapshot] = Field(default_factory=list)
    company_introductions: list[CompanyIntroduction] = Field(default_factory=list)
    executive_brief: list[ExecutiveFinding] = Field(default_factory=list)
    five_forces: FiveForces = Field(default_factory=FiveForces)
    bmc: BusinessModelCanvas = Field(default_factory=BusinessModelCanvas)
    vrio: list[VRIOResource] = Field(default_factory=list)
    positioning: PositioningMap = Field(default_factory=PositioningMap)
    swot: SWOT = Field(default_factory=SWOT)
    comparison: ComparisonTable = Field(default_factory=ComparisonTable)
    recommendations: list[Recommendation] = Field(default_factory=list)
    audience_implications: AudienceImplications | None = None
    gaps: list[CoverageGap] = Field(default_factory=list)
