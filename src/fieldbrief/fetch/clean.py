"""Clean fetched HTML into plain text, and fingerprint it for dedup.

design.md §4: SOURCES.content_hash — "skip re-extraction if content_hash is unchanged".
"""

import hashlib
from collections.abc import Iterable
from datetime import date

import trafilatura

from fieldbrief.consts import EXTRACTION_MAX_CHARS


def clean_html(html: str) -> str | None:
    """Extract the main article/body text, dropping nav/ads/boilerplate. None if too thin to use."""
    extracted = trafilatura.extract(html, favor_recall=True)
    if extracted is None or len(extracted.strip()) < 100:
        return None
    return extracted.strip()


def content_hash(text: str) -> str:
    """Stable fingerprint of cleaned text — used to skip re-extraction on unchanged pages."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_metadata(html: str) -> tuple[str | None, date | None]:
    """Best-effort (title, published_date) from page metadata (OpenGraph /
    schema.org / meta tags), via trafilatura's metadata pass — local parsing,
    no extra network call. Either value can come back None; callers should
    treat that as "unknown," not an error."""
    meta = trafilatura.extract_metadata(html)
    if meta is None:
        return None, None
    title = meta.title or None
    published: date | None = None
    if meta.date:
        try:
            published = date.fromisoformat(meta.date[:10])
        except ValueError:
            published = None
    return title, published


def select_relevant_passages(
    text: str, keywords: Iterable[str], max_chars: int = EXTRACTION_MAX_CHARS
) -> str:
    """Bounded, relevance-ranked passage selection — scores each paragraph
    by keyword overlap and greedily keeps the highest-scoring ones that fit
    the budget, then restores their original order."""
    if len(text) <= max_chars:
        return text
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return text[:max_chars]

    kw = {k.lower() for k in keywords if k and len(k) > 2}

    def score(paragraph: str) -> int:
        lower = paragraph.lower()
        return sum(lower.count(k) for k in kw)

    ranked = sorted(range(len(paragraphs)), key=lambda i: score(paragraphs[i]), reverse=True)
    selected: list[int] = []
    total = 0
    for i in ranked:
        length = len(paragraphs[i])
        if total + length > max_chars:
            continue
        selected.append(i)
        total += length + 2
    if not selected:
        return text[:max_chars]
    selected.sort()
    return "\n\n".join(paragraphs[i] for i in selected)
