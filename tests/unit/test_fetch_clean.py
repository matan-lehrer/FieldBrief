from fieldbrief.fetch.clean import clean_html, content_hash

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
