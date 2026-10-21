"""Ground-truth file format for evals/golden/*.yaml (ADR 0011)."""

from pydantic import BaseModel, Field


class Fact(BaseModel):
    value: str
    source_url: str
    tolerance: str | None = Field(
        default=None, description="Free-text tolerance note for numeric facts, e.g. '+/- 15%'"
    )


class GoldenCompany(BaseModel):
    name: str
    public: bool
    industry: str
    facts: dict[str, Fact] = Field(default_factory=dict)
    competitors: list[str] = Field(default_factory=list)
    key_points: dict[str, list[str]] = Field(default_factory=dict)
    known_gaps: list[str] = Field(default_factory=list)
