import typer

app = typer.Typer(
    name="fieldbrief",
    help="AI-powered competitive analysis, built on real strategy frameworks.",
    no_args_is_help=True,
)


@app.command()
def analyze(
    company: str = typer.Argument(..., help="Company name to analyze, e.g. 'Wix'"),
    competitors: int = typer.Option(4, "--competitors", help="Number of competitors (0 for none)"),
) -> None:
    """Analyze a company and produce a cited strategy report."""
    typer.echo(
        f"analyze is not implemented yet (stage 3) — would research '{company}' "
        f"with {competitors} competitor(s)."
    )
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the installed fieldbrief version."""
    from importlib.metadata import version as pkg_version

    typer.echo(pkg_version("fieldbrief"))


if __name__ == "__main__":
    app()
