"""Package entry point for the `sup` console script.

Holds the process's single `logging.basicConfig()` call; every other
module obtains its logger through `sup.util.register()`.
"""
import sup.cli
import logging

logging.basicConfig(level=logging.INFO)

def main() -> None:
    sup.cli.app()
    