import uuid
from typing import Literal

from fieldbrief.db.models import Claim, Source
from fieldbrief.reports.schema import Citation, Confidence

EvidenceStatus = Literal["strong", "supported", "unverified", "conflicting"]

SOURCE_RELIABILITY = {
    "filing": 1.0,
    "official": 1.0,
    "news": 0.8,
    "blog": 0.5,
}

CORROBORATION_BONUS_PER_SOURCE = 0.05

CONFLICT_RELATIVE_TOLERANCE = 0.15


def _numeric_value(claim: Claim) -> float | None:
    for v in claim.value.values():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _claims_conflict(claims: list[Claim]) -> bool:
    values = [v for v in (_numeric_value(c) for c in claims) if v is not None]
    if len(values) < 2:
        return False
    lo, hi = min(values), max(values)
    if lo == 0:
        return hi != 0
    return (hi - lo) / abs(lo) > CONFLICT_RELATIVE_TOLERANCE


def _evidence_status(*, verified: bool, corroboration: int, conflicting: bool) -> EvidenceStatus:
    if conflicting:
        return "conflicting"
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
    conflicting = corroboration > 1 and _claims_conflict(claims)
    bonus = (
        0.0
        if conflicting
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
                verified=verified, corroboration=corroboration, conflicting=conflicting
            ),
        ),
    )
