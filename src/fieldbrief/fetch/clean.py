import hashlib
from collections.abc import Iterable
from datetime import date

import trafilatura

from fieldbrief.consts import EXTRACTION_MAX_CHARS


def clean_html(html: str) -> str | None:
    extracted = trafilatura.extract(html, favor_recall=True)
    if extracted is None or len(extracted.strip()) < 100:
        return None
    return extracted.strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_metadata(html: str) -> tuple[str | None, date | None]:
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
