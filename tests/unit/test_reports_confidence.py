import uuid

import pytest

from fieldbrief.db.models import Claim, Source
from fieldbrief.reports.confidence import compute_citation


def _claim(
    source_id: uuid.UUID, relevance: float, verified: bool, value: dict | None = None
) -> Claim:
    claim = Claim(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        source_id=source_id,
        field="profile.description",
        statement="x",
        value=value or {},
        snippet="x",
        relevance=relevance,
        verified=verified,
    )
    return claim


def _source(source_type: str) -> Source:
    return Source(id=uuid.uuid4(), url="https://x.com", source_type=source_type, content_hash="h")


def test_single_verified_official_source_scores_high() -> None:
    source = _source("official")
    claim = _claim(source.id, relevance=1.0, verified=True)

    citation = compute_citation([claim], {source.id: source})

    assert citation.confidence.score == pytest.approx(1.0)
    assert citation.confidence.verified is True
    assert citation.confidence.corroboration == 1


def test_unverified_claim_scores_lower_than_verified() -> None:
    source = _source("official")
    verified_claim = _claim(source.id, relevance=1.0, verified=True)
    unverified_claim = _claim(source.id, relevance=1.0, verified=False)

    verified_citation = compute_citation([verified_claim], {source.id: source})
    unverified_citation = compute_citation([unverified_claim], {source.id: source})

    assert unverified_citation.confidence.score < verified_citation.confidence.score


def test_blog_source_scores_lower_than_official() -> None:
    official = _source("official")
    blog = _source("blog")
    official_claim = _claim(official.id, relevance=1.0, verified=True)
    blog_claim = _claim(blog.id, relevance=1.0, verified=True)

    official_citation = compute_citation([official_claim], {official.id: official})
    blog_citation = compute_citation([blog_claim], {blog.id: blog})

    assert blog_citation.confidence.score < official_citation.confidence.score


def test_corroboration_from_multiple_sources_increases_score() -> None:
    blog1 = _source("blog")
    blog2 = _source("blog")
    claim1 = _claim(blog1.id, relevance=0.8, verified=True)
    claim2 = _claim(blog2.id, relevance=0.8, verified=True)

    single_citation = compute_citation([claim1], {blog1.id: blog1})
    corroborated_citation = compute_citation([claim1, claim2], {blog1.id: blog1, blog2.id: blog2})

    assert corroborated_citation.confidence.score > single_citation.confidence.score
    assert corroborated_citation.confidence.corroboration == 2


def test_score_never_exceeds_one() -> None:
    sources = {uuid.uuid4(): _source("official") for _ in range(10)}
    claims = [_claim(sid, relevance=1.0, verified=True) for sid in sources]

    citation = compute_citation(claims, sources)
    assert citation.confidence.score <= 1.0


def test_requires_at_least_one_claim() -> None:
    with pytest.raises(ValueError, match="at least one claim"):
        compute_citation([], {})


def test_agreeing_numeric_sources_corroborate_and_score_strong() -> None:
    source1 = _source("official")
    source2 = _source("news")
    claim1 = _claim(source1.id, relevance=1.0, verified=True, value={"amount_usd": 10_000_000})
    claim2 = _claim(source2.id, relevance=1.0, verified=True, value={"amount_usd": 10_200_000})

    citation = compute_citation([claim1, claim2], {source1.id: source1, source2.id: source2})

    assert citation.confidence.status == "strong"
    assert citation.confidence.corroboration == 2


def test_conflicting_numeric_sources_dont_get_a_corroboration_bonus() -> None:
    """Two sources quoting very different numbers for the same field aren't
    mutual corroboration — distinct URLs agreeing by coincidence that
    *something* was reported isn't the same as agreeing on the value."""
    # Reliability < 1.0 (news, not official) leaves headroom for the
    # corroboration bonus to actually move the score either way.
    source1 = _source("news")
    source2 = _source("news")
    claim1 = _claim(source1.id, relevance=0.9, verified=True, value={"amount_usd": 10_000_000})
    claim2 = _claim(source2.id, relevance=0.9, verified=True, value={"amount_usd": 50_000_000})

    agreeing1 = _claim(source1.id, relevance=0.9, verified=True, value={"amount_usd": 10_000_000})
    agreeing2 = _claim(source2.id, relevance=0.9, verified=True, value={"amount_usd": 10_200_000})

    conflicting_citation = compute_citation(
        [claim1, claim2], {source1.id: source1, source2.id: source2}
    )
    agreeing_citation = compute_citation(
        [agreeing1, agreeing2], {source1.id: source1, source2.id: source2}
    )

    assert conflicting_citation.confidence.status == "conflicting"
    # Same corroboration count either way — only the score/status reflect
    # whether the sources actually agree.
    assert conflicting_citation.confidence.corroboration == 2
    assert conflicting_citation.confidence.score < agreeing_citation.confidence.score


def test_unverified_claim_status_is_unverified() -> None:
    source = _source("official")
    claim = _claim(source.id, relevance=1.0, verified=False)

    citation = compute_citation([claim], {source.id: source})

    assert citation.confidence.status == "unverified"


def test_single_verified_source_status_is_supported() -> None:
    source = _source("official")
    claim = _claim(source.id, relevance=1.0, verified=True)

    citation = compute_citation([claim], {source.id: source})

    assert citation.confidence.status == "supported"


def test_claims_for_different_entities_are_different_scope_not_conflicting() -> None:
    """A subsidiary's standalone revenue and its parent's consolidated
    revenue aren't a 'conflict' just because they share a field name and
    disagree numerically — they're two different facts (the Synamedia
    regression: £0-1M standalone vs. a much larger group-wide figure)."""
    source1 = _source("filing")
    source2 = _source("filing")
    claim1 = _claim(
        source1.id,
        relevance=1.0,
        verified=True,
        value={
            "amount_usd": 500_000,
            "entity_name": "Synamedia Holdings Limited",
            "scope": "standalone",
        },
    )
    claim2 = _claim(
        source2.id,
        relevance=1.0,
        verified=True,
        value={
            "amount_usd": 400_000_000,
            "entity_name": "Synamedia Group",
            "scope": "consolidated",
        },
    )

    citation = compute_citation([claim1, claim2], {source1.id: source1, source2.id: source2})

    assert citation.confidence.status == "different_scope"


def test_claims_for_the_same_entity_still_conflict_normally() -> None:
    """Scope tagging must not suppress a genuine conflict between two
    claims that actually describe the same entity/period/currency."""
    source1 = _source("news")
    source2 = _source("news")
    claim1 = _claim(
        source1.id,
        relevance=0.9,
        verified=True,
        value={"amount_usd": 10_000_000, "entity_name": "Acme Inc", "scope": "standalone"},
    )
    claim2 = _claim(
        source2.id,
        relevance=0.9,
        verified=True,
        value={"amount_usd": 50_000_000, "entity_name": "Acme Inc", "scope": "standalone"},
    )

    citation = compute_citation([claim1, claim2], {source1.id: source1, source2.id: source2})

    assert citation.confidence.status == "conflicting"


def test_scope_tags_only_block_comparison_when_they_actually_disagree() -> None:
    """A claim with no scope tags at all (unknown) must still be comparable
    against one that does have them — unknown is compatible with anything,
    it just isn't informative either way."""
    source1 = _source("official")
    source2 = _source("news")
    claim1 = _claim(source1.id, relevance=1.0, verified=True, value={"amount_usd": 10_000_000})
    claim2 = _claim(
        source2.id,
        relevance=0.9,
        verified=True,
        value={"amount_usd": 10_200_000, "entity_name": "Acme Inc"},
    )

    citation = compute_citation([claim1, claim2], {source1.id: source1, source2.id: source2})

    assert citation.confidence.status == "strong"
