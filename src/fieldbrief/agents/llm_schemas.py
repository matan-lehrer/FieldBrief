"""Pydantic schemas for the LLM calls inside the agent graph — distinct from
reports/schema.py (the report's shape). These are what the model itself
returns; the graph nodes translate between the two."""

from typing import Any, Literal

from pydantic import BaseModel, Field

BMC_BLOCKS = Literal[
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


class ExtractedClaim(BaseModel):
    field: str
    statement: str = Field(description="A short, human-readable statement of the fact")
    value: dict[str, Any] = Field(
        default_factory=dict, description="Structured bits if any (e.g. {'amount_usd': 412000000})"
    )
    relevance: float = Field(
        ge=0, le=1, description="Is this page genuinely about the company, or a passing mention?"
    )
    excerpt: str | None = Field(
        default=None,
        description=(
            "An exact, verbatim short quote (one or two sentences) copied "
            "character-for-character from the page text that supports this claim. "
            "Never paraphrase here — omit if no single short passage directly "
            "supports it."
        ),
    )
    bmc_block: BMC_BLOCKS | None = Field(
        default=None,
        description=(
            "Only if this claim ALSO maps to a Business Model Canvas block (ADR 0009) — "
            "independent of `field`. For cost_structure specifically: the claim must describe "
            "what actually drives cost (e.g. 'cloud infrastructure is its largest expense', "
            "'labor-intensive service delivery') — a bare margin/profitability number alone "
            "is not cost-structure evidence."
        ),
    )


class ExtractionResult(BaseModel):
    """One page -> zero or more claims. No claim for a field the page doesn't
    support — inventing one is worse than leaving it out (CLAUDE.md hard
    constraint 5)."""

    claims: list[ExtractedClaim] = Field(default_factory=list)


class ClaimVerification(BaseModel):
    verified: bool
    reason: str = Field(
        description="One sentence: does the snippet actually support the statement?"
    )


class SWOTItemDraft(BaseModel):
    statement: str
    derived_from: Literal[
        "events", "market_size", "regulation", "bmc", "financials", "risks", "other"
    ] = Field(
        description=(
            "five_forces and vrio are never valid here — those SWOT items are derived "
            "deterministically from your five_forces/vrio answers below, not authored directly. "
            "Use 'financials' or 'risks' for an internal capability signal (e.g. shrinking "
            "margins, customer concentration, a disclosed risk factor) rather than an external "
            "market one."
        )
    )
    source_claim_indices: list[int] = Field(
        min_length=1,
        description="Indices into the numbered claims list in the prompt — never invent one",
    )


class RecommendationDraft(BaseModel):
    statement: str
    rationale: str
    priority: Literal["high", "medium", "low"]
    source_claim_indices: list[int] = Field(min_length=1)


class ForceAssessmentDraft(BaseModel):
    force: Literal[
        "buyer_power", "supplier_power", "substitutes", "threat_of_new_entrants", "rivalry"
    ]
    rating: Literal["low", "medium", "high", "insufficient_evidence"] = Field(
        description=(
            "Pressure intensity on the company from this force — high is always bad for the "
            "company. For threat_of_new_entrants specifically: high means entry is EASY (low "
            "barriers), so new entrants are a real threat; low means barriers are high, so the "
            "threat is low. Use insufficient_evidence if the claims mention this force but are "
            "too thin to confidently call a direction — never guess a bucket."
        )
    )
    rationale: str
    source_claim_indices: list[int] = Field(min_length=1)


class VRIOResourceDraft(BaseModel):
    resource: str
    valuable: bool
    rare: bool
    inimitable: bool
    organized: bool
    rationale: str
    source_claim_indices: list[int] = Field(min_length=1)


class PositionedCompanyDraft(BaseModel):
    company_index: int = Field(description="Index into the numbered companies list in the prompt")
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    source_claim_indices: list[int] = Field(
        min_length=1,
        description="Indices into the numbered claims list — never place a company with no "
        "evidence behind its position; omit it instead.",
    )


class PositioningDraft(BaseModel):
    x_axis_rationale: str
    y_axis_rationale: str
    companies: list[PositionedCompanyDraft] = Field(default_factory=list)


class FrameworkSynthesisResult(BaseModel):
    """The one strong-model call that produces every cross-company judgment
    (design.md's cost model assumes one synthesis call, not four — ADR 0008)."""

    five_forces: list[ForceAssessmentDraft] = Field(default_factory=list)
    vrio: list[VRIOResourceDraft] = Field(default_factory=list)
    positioning: PositioningDraft | None = None
    other_strengths: list[SWOTItemDraft] = Field(default_factory=list)
    other_weaknesses: list[SWOTItemDraft] = Field(default_factory=list)
    other_opportunities: list[SWOTItemDraft] = Field(default_factory=list)
    other_threats: list[SWOTItemDraft] = Field(default_factory=list)
    recommendations: list[RecommendationDraft] = Field(default_factory=list)
