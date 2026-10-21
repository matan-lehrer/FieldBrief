"""Confidence scoring: design.md §3's qualitative model, made concrete.

confidence = f(source_reliability, relevance, verified, corroboration) — derived
at report-build time, never stored (design.md invariant).
"""

import uuid

from fieldbrief.db.models import Claim, Source
from fieldbrief.reports.schema import Citation, Confidence

SOURCE_RELIABILITY = {
    "filing": 1.0,
    "official": 1.0,
    "news": 0.8,
    "blog": 0.5,
}

# Each additional independent source corroborating the same field nudges
# confidence up, capped so it never exceeds 1.0.
CORROBORATION_BONUS_PER_SOURCE = 0.05


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
    bonus = min(CORROBORATION_BONUS_PER_SOURCE * (corroboration - 1), 1.0 - base_score)
    score = min(1.0, base_score + bonus)

    return Citation(
        claim_ids=[claim.id for claim in claims],
        confidence=Confidence(
            score=round(score, 3),
            corroboration=corroboration,
            verified=any(claim.verified for claim in claims),
        ),
    )
