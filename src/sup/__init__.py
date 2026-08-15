"""Package entry point for the `sup` console script.

Holds the process's single `logging.basicConfig()` call; every other
module obtains its logger through `sup.util.register()`.
"""

from sup.boostrap import bootstrap
from sup.util import register
import sup.cli
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = register(logging.getLogger(__name__), "main")

def main() -> None:
    """Create the database schemas, then hand control to the Typer app.

    Bound to the `sup` console script in `pyproject.toml` and called
    synchronously. The `asyncio.run()` loop closes before the CLI starts.
    """
    logger.info("Boostrapping databases...")
    asyncio.run(bootstrap())
    logger.info("Starting application...")
    sup.cli.app()
    