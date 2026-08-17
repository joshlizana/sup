"""Root Typer app / `sup` entry point. Subcommands (ingest, transform, tui,
dashboard) attach to `app` as they're built."""

import typer
import logging
import asyncio
from sup.boostrap import bootstrap_ingest, bootstrap_transform
from sup.services.ingest.ingest import Ingester

app = typer.Typer()
logger = logging.getLogger(__name__)

@app.command()
def ingest():
    
    logger.info("Boostrapping ingest databases...")
    asyncio.run(bootstrap_ingest())
    logger.info("Starting ingest...")

    async def _run():
        async with Ingester() as ingester:
            await ingester.run()
    try:
        asyncio.run(_run())
    except RuntimeError as e:
        print(f"Error: {e}")
        raise typer.Exit(code=1)

@app.command()
def transform():
    logger.info("Bootstrapping transform databases...")
    asyncio.run(bootstrap_transform())
    logger.info("Starting transform...")


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
