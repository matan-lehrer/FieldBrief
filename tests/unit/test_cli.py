from typer.testing import CliRunner

from fieldbrief.cli import app

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "analyze" in result.stdout


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.8.0"


def test_analyze_rejects_invalid_tier_without_any_io() -> None:
    """Typer/Click validates --tier against the Tier literal before `analyze`'s
    body ever runs — this must exit without touching Wikidata/DB/LLM. The real
    pipeline (any tier, with/without competitors) is covered by
    tests/integration/test_agent_graph.py, with those layers mocked."""
    result = runner.invoke(app, ["analyze", "Wix", "--tier", "bogus"])
    assert result.exit_code == 2
    assert "not one of" in result.output
