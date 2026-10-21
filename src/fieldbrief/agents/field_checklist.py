"""The field checklist (docs/field-checklist.md), as code. Mirrors that table
exactly — update both together.
"""

from dataclasses import dataclass
from typing import Literal

Tier = Literal["quick", "standard", "deep"]
TIER_ORDER: dict[Tier, int] = {"quick": 0, "standard": 1, "deep": 2}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    tier: Tier
    target_only: bool  # False = also researched for competitors (the "light subset")


FIELD_CHECKLIST: list[FieldSpec] = [
    FieldSpec("profile.description", "quick", False),
    FieldSpec("profile.hq_founded", "quick", False),
    FieldSpec("profile.employees", "quick", False),
    FieldSpec("offering.products", "quick", False),
    FieldSpec("customers.segments", "quick", False),
    FieldSpec("revenue.model", "quick", False),
    FieldSpec("financials.revenue", "quick", False),
    FieldSpec("events.recent", "quick", True),
    FieldSpec("pricing.model", "standard", False),
    FieldSpec("offering.value_prop", "standard", False),
    FieldSpec("channels.go_to_market", "standard", True),
    FieldSpec("financials.profitability", "standard", True),
    FieldSpec("financials.funding_or_cap", "standard", False),
    FieldSpec("resources.advantages", "standard", True),
    FieldSpec("market.forces", "standard", True),
    FieldSpec("environment.regulation", "standard", True),
    FieldSpec("market.size_growth", "deep", True),
    FieldSpec("partners.key", "deep", True),
    FieldSpec("strategy.moves", "deep", False),
    FieldSpec("risks.disclosed", "deep", True),
]

# Effort tiers (design.md §1): competitor counts.
TIER_COMPETITOR_COUNT: dict[Tier, int] = {"quick": 2, "standard": 4, "deep": 6}

# Agent graph stop conditions (ADR 0010): retry rounds per tier.
MAX_RETRY_ROUNDS: dict[Tier, int] = {"quick": 1, "standard": 2, "deep": 3}


def fields_for(tier: Tier, is_target: bool) -> list[str]:
    """Every field due for research at this tier (cumulative) and company role."""
    max_rank = TIER_ORDER[tier]
    return [
        spec.name
        for spec in FIELD_CHECKLIST
        if TIER_ORDER[spec.tier] <= max_rank and (is_target or not spec.target_only)
    ]
