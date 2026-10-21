"""Clean fetched HTML into plain text, and fingerprint it for dedup.

design.md §4: SOURCES.content_hash — "skip re-extraction if content_hash is unchanged".
"""

import hashlib

import trafilatura


def clean_html(html: str) -> str | None:
    """Extract the main article/body text, dropping nav/ads/boilerplate. None if too thin to use."""
    extracted = trafilatura.extract(html, favor_recall=True)
    if extracted is None or len(extracted.strip()) < 100:
        return None
    return extracted.strip()


def content_hash(text: str) -> str:
    """Stable fingerprint of cleaned text — used to skip re-extraction on unchanged pages."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
