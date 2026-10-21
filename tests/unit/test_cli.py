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
    assert result.stdout.strip() == "0.1.0"


def test_analyze_rejects_nonzero_competitors_without_any_io() -> None:
    """Stage 3 only supports --competitors 0 (stage 4 adds real competitors).
    This must exit before touching Wikidata/DB/LLM — the full pipeline is
    covered by tests/integration/test_agent_graph.py, with those layers mocked."""
    result = runner.invoke(app, ["analyze", "Wix", "--competitors", "2"])
    assert result.exit_code == 1
    assert "aren't supported yet" in result.stdout
