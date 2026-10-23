import uuid
from itertools import combinations
from typing import Literal

from fieldbrief.db.models import Claim, Source
from fieldbrief.reports.schema import Citation, Confidence

EvidenceStatus = Literal["strong", "supported", "unverified", "conflicting", "different_scope"]

SOURCE_RELIABILITY = {
    "filing": 1.0,
    "official": 1.0,
    "news": 0.8,
    "blog": 0.5,
}

CORROBORATION_BONUS_PER_SOURCE = 0.05

CONFLICT_RELATIVE_TOLERANCE = 0.15

# Keys a claim's value dict may carry to say what a number actually refers to
# (agents/prompts.py asks the extractor to tag these for financial fields).
# Two claims are only compared as the same fact if these agree wherever both
# are known — e.g. a subsidiary's standalone revenue is not the same fact as
# its parent's consolidated revenue, even if both are tagged "financials.revenue".
SCOPE_KEYS = ("entity_name", "currency", "period", "scope")


def _numeric_values_by_key(claim: Claim) -> dict[str, float]:
    return {
        k: float(v)
        for k, v in claim.value.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }


def _scope_signature(claim: Claim) -> tuple[str | None, ...]:
    return tuple(
        str(claim.value[k]) if claim.value.get(k) not in (None, "unknown") else None
        for k in SCOPE_KEYS
    )


def _scope_compatible(a: tuple[str | None, ...], b: tuple[str | None, ...]) -> bool:
    return all(x is None or y is None or x == y for x, y in zip(a, b, strict=True))


def _values_differ(v1: float, v2: float) -> bool:
    lo, hi = min(v1, v2), max(v1, v2)
    if lo == 0:
        return hi != 0
    return (hi - lo) / abs(lo) > CONFLICT_RELATIVE_TOLERANCE


def _compare_claims(claims: list[Claim]) -> tuple[bool, bool]:
    """Pairwise-compares every two claims that share a numeric value key.
    Returns (conflicting, different_scope): a same-scope disagreement is a
    real conflict; a disagreement between incompatible scopes (different
    entity/currency/period) isn't a conflict at all — it's two different
    facts that happen to share a field name, so it's flagged separately
    instead of silently averaged into one confident answer."""
    conflicting = False
    different_scope = False
    for c1, c2 in combinations(claims, 2):
        values1, values2 = _numeric_values_by_key(c1), _numeric_values_by_key(c2)
        shared_keys = set(values1) & set(values2)
        if not shared_keys:
            continue
        compatible = _scope_compatible(_scope_signature(c1), _scope_signature(c2))
        for key in shared_keys:
            if not _values_differ(values1[key], values2[key]):
                continue
            if compatible:
                conflicting = True
            else:
                different_scope = True
    return conflicting, different_scope


def _evidence_status(
    *, verified: bool, corroboration: int, conflicting: bool, different_scope: bool
) -> EvidenceStatus:
    if conflicting:
        return "conflicting"
    if different_scope:
        return "different_scope"
    if not verified:
        return "unverified"
    return "strong" if corroboration > 1 else "supported"


def compute_citation(claims: list[Claim], sources: dict[uuid.UUID, Source]) -> Citation:
    if not claims:
        raise ValueError("compute_citation requires at least one claim")

    per_claim_scores = []
    for claim in claims:
        source = sources[claim.source_id]
        reliability = SOURCE_RELIABILITY.get(source.source_type, 0.5)
        verified_multiplier = 1.0 if claim.verified else 0.5
        per_claim_scores.append(reliability * claim.relevance * verified_multiplier)

    base_score = max(per_claim_scores)
    corroboration = len({claim.source_id for claim in claims})
    conflicting, different_scope = _compare_claims(claims) if corroboration > 1 else (False, False)
    bonus = (
        0.0
        if conflicting or different_scope
        else min(CORROBORATION_BONUS_PER_SOURCE * (corroboration - 1), 1.0 - base_score)
    )
    score = min(1.0, base_score + bonus)
    verified = any(claim.verified for claim in claims)

    return Citation(
        claim_ids=[claim.id for claim in claims],
        confidence=Confidence(
            score=round(score, 3),
            corroboration=corroboration,
            verified=verified,
            status=_evidence_status(
                verified=verified,
                corroboration=corroboration,
                conflicting=conflicting,
                different_scope=different_scope,
            ),
        ),
    )
