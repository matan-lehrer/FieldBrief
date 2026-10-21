import uuid

import pytest

from fieldbrief.db.models import Claim, Source
from fieldbrief.reports.confidence import compute_citation


def _claim(source_id: uuid.UUID, relevance: float, verified: bool) -> Claim:
    claim = Claim(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        source_id=source_id,
        field="profile.description",
        statement="x",
        value={},
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
