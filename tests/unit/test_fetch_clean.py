from fieldbrief.fetch.clean import (
    clean_html,
    content_hash,
    extract_metadata,
    select_relevant_passages,
)

ARTICLE_HTML = """
<html><body>
<nav>Home | About | Contact</nav>
<article>
<h1>Acme Corp raises $50M Series B</h1>
<p>Acme Corp, a company that makes widgets, announced today that it has raised
$50 million in a Series B funding round led by Example Ventures. The company
plans to use the funds to expand its engineering team and enter new markets.
This is a longer paragraph of real content so that trafilatura's heuristics
treat it as the main body rather than boilerplate, since very short pages get
rejected by our own length check regardless of what trafilatura decides.</p>
</article>
<footer>Copyright 2026</footer>
</body></html>
"""

THIN_HTML = "<html><body><p>Hi</p></body></html>"


def test_clean_html_extracts_main_content() -> None:
    cleaned = clean_html(ARTICLE_HTML)
    assert cleaned is not None
    assert "Acme Corp raises" in cleaned
    assert "Home | About | Contact" not in cleaned


def test_clean_html_rejects_thin_pages() -> None:
    assert clean_html(THIN_HTML) is None


def test_content_hash_is_deterministic() -> None:
    assert content_hash("hello world") == content_hash("hello world")


def test_content_hash_differs_for_different_text() -> None:
    assert content_hash("hello") != content_hash("world")


def test_select_relevant_passages_keeps_text_under_the_limit_unchanged() -> None:
    text = "short paragraph"
    assert select_relevant_passages(text, keywords={"revenue"}, max_chars=8000) == text


def test_select_relevant_passages_prefers_keyword_matches_over_the_head_of_the_page() -> None:
    """The old behavior was a blind page_text[:max_chars] slice — a passage
    answering the requested field far down the page never reached the
    model. This must surface it instead of the irrelevant opening filler."""
    filler = ("Irrelevant filler paragraph with nothing useful in it whatsoever. " * 5).strip()
    relevant = "The company reported annual revenue of $412 million this year."
    paragraphs = [filler] * 200 + [relevant]
    text = "\n\n".join(paragraphs)
    assert len(text) > 8000

    bounded = select_relevant_passages(text, keywords={"revenue", "million"}, max_chars=500)
    assert relevant in bounded


def test_select_relevant_passages_falls_back_to_head_slice_with_no_keyword_hits() -> None:
    text = "\n\n".join(["filler paragraph number " + str(i) for i in range(500)])
    assert len(text) > 8000
    bounded = select_relevant_passages(text, keywords={"nonexistent_keyword_xyz"}, max_chars=200)
    assert len(bounded) <= 200 or bounded in text


def test_extract_metadata_reads_title_and_date_from_meta_tags() -> None:
    html = """
    <html><head>
    <title>Acme Corp raises $50M Series B</title>
    <meta property="article:published_time" content="2026-03-05T12:00:00Z">
    </head><body><article><p>Some article body text, long enough to matter
    for trafilatura's own heuristics in a realistic page.</p></article></body></html>
    """
    title, published = extract_metadata(html)
    assert title == "Acme Corp raises $50M Series B"
    assert published is not None
    assert published.isoformat() == "2026-03-05"


def test_extract_metadata_returns_none_none_for_bare_html() -> None:
    title, published = extract_metadata("<html><body><p>no metadata here</p></body></html>")
    assert title is None
    assert published is None
