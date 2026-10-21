"""Pydantic schemas for the LLM calls inside the agent graph — distinct from
reports/schema.py (the report's shape). These are what the model itself
returns; the graph nodes translate between the two."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ExtractedClaim(BaseModel):
    field: str
    statement: str = Field(description="A short, human-readable statement of the fact")
    value: dict[str, Any] = Field(
        default_factory=dict, description="Structured bits if any (e.g. {'amount_usd': 412000000})"
    )
    relevance: float = Field(
        ge=0, le=1, description="Is this page genuinely about the company, or a passing mention?"
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
        "five_forces", "vrio", "events", "bmc", "market_size", "regulation", "other"
    ]
    source_claim_indices: list[int] = Field(
        description=(
            "Indices into the numbered claims list provided in the prompt — never invent an index"
        )
    )


class RecommendationDraft(BaseModel):
    statement: str
    rationale: str
    priority: Literal["high", "medium", "low"]
    source_claim_indices: list[int]
    swot_indices: list[int] = Field(
        min_length=1,
        description=(
            "Indices into the combined opportunities+threats list this recommendation "
            "below is built from, opportunities first then threats (e.g. if there are 2 "
            "opportunities and 1 threat, valid indices are 0, 1, 2). Every recommendation "
            "must address at least one — never an empty list."
        ),
    )


class SynthesisResult(BaseModel):
    opportunities: list[SWOTItemDraft] = Field(default_factory=list)
    threats: list[SWOTItemDraft] = Field(default_factory=list)
    recommendations: list[RecommendationDraft] = Field(default_factory=list)
