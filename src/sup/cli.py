"""Root Typer app / `sup` entry point. Subcommands (ingest, transform, tui,
dashboard) attach to `app` as they're built."""

import typer

app = typer.Typer()

@app.callback(invoke_without_command=True)
def sup(ctx: typer.Context) -> None:
    """Handle bare `sup`, invoked with no subcommand.

    Runs ahead of any subcommand as well, so the body checks
    `invoked_subcommand` to tell the two cases apart. Bare `sup` will
    orchestrate ingest, transform, dashboard, and the TUI (TDD-0002); it
    currently greets and exits.
    """
    if ctx.invoked_subcommand is None:
        typer.echo("Welcome to the Sup CLI!")
