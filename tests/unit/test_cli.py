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


def test_analyze_stub_exits_nonzero() -> None:
    result = runner.invoke(app, ["analyze", "Wix"])
    assert result.exit_code == 1
    assert "Wix" in result.stdout
