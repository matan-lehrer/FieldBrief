"""Confidence scoring: design.md §3's qualitative model, made concrete.

confidence = f(source_reliability, relevance, verified, corroboration) — derived
at report-build time, never stored (design.md invariant).
"""

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

# Each additional independent source corroborating the same field nudges
# confidence up, capped so it never exceeds 1.0.
CORROBORATION_BONUS_PER_SOURCE = 0.05

# Two sources' numeric values for the same field disagreeing by more than
# this (relative to the smaller one) are conflicting evidence, not mutual
# corroboration — distinct URLs agreeing by coincidence on wildly different
# numbers was getting the same bonus as two sources that actually agree.
CONFLICT_RELATIVE_TOLERANCE = 0.15


def _numeric_value(claim: Claim) -> float | None:
    """Best-effort: the first numeric leaf in claim.value, if any. Only used
    to detect disagreement between sources — never to pick a display value,
    which stays whatever analyze_node/synthesis already chose."""
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
    """A status a reader can actually act on, instead of a bare score that
    reads like a probability of being true. `score` is kept too (eval/
    internal ordering), but this is what the UI/PDF should show."""
    if conflicting:
        return "conflicting"
    if not verified:
        return "unverified"
    return "strong" if corroboration > 1 else "supported"


def compute_citation(claims: list[Claim], sources: dict[uuid.UUID, Source]) -> Citation:
    """One field's claims -> a Citation with a derived confidence score.

    `sources` maps claim.source_id -> Source for every claim passed in; the
    caller already has these loaded (no surprise queries from inside here).
    """
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
