from dataclasses import dataclass
from typing import Literal

from fieldbrief.config import settings

Tier = Literal["quick", "standard", "deep"]
TIER_ORDER: dict[Tier, int] = {"quick": 0, "standard": 1, "deep": 2}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    tier: Tier
    target_only: bool


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
    FieldSpec("financials.funding", "standard", False),
    FieldSpec("financials.market_cap_or_valuation", "standard", False),
    FieldSpec("resources.advantages", "standard", True),
    FieldSpec("market.forces", "standard", True),
    FieldSpec("environment.regulation", "standard", True),
    FieldSpec("market.size_growth", "deep", True),
    FieldSpec("partners.key", "deep", True),
    FieldSpec("strategy.moves", "deep", False),
    FieldSpec("risks.disclosed", "deep", True),
]

TIER_COMPETITOR_COUNT: dict[Tier, int] = {"quick": 2, "standard": 4, "deep": 6}

MAX_RETRY_ROUNDS: dict[Tier, int] = {"quick": 1, "standard": 4, "deep": 6}

TIER_BUDGET_MULTIPLIER: dict[Tier, float] = {"quick": 0.2, "standard": 1.0, "deep": 4.0}
TIER_TIME_MULTIPLIER: dict[Tier, float] = {"quick": 0.25, "standard": 1.0, "deep": 2.5}


def budget_usd_for(tier: Tier) -> float:
    return settings.report_budget_usd_standard * TIER_BUDGET_MULTIPLIER[tier]


def time_budget_seconds_for(tier: Tier) -> float:
    return settings.report_time_budget_seconds * TIER_TIME_MULTIPLIER[tier]


def fields_for(tier: Tier, is_target: bool) -> list[str]:
    max_rank = TIER_ORDER[tier]
    return [
        spec.name
        for spec in FIELD_CHECKLIST
        if TIER_ORDER[spec.tier] <= max_rank and (is_target or not spec.target_only)
    ]
