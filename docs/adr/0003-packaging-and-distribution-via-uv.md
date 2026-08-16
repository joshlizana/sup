# ADR-0003: Packaging and distribution via uv

## Status

Accepted

## Options considered

- **`pip` with `requirements.txt` or a plain `setup.py`.** Familiar, but
  slower resolution and no lockfile story by default, leaving room for drift
  between the author's environment and a stranger's.
- **`uv` with `pyproject.toml`, installed via `uv tool install` or `uvx`.**
  Fast reproducible resolution with a lockfile, and a one-command
  install-and-run path. Chosen.

## Decision

Package with `pyproject.toml`, define a console-script entry point, and
target `uv` as the primary documented install path. `pip`-compatible
packaging keeps working, since `pyproject.toml` is not `uv`-exclusive, but
`uv` is what gets documented and tested against.

## Why

A stranger has to be able to install and run `sup` without friction, and a
fiddly install undermines the project regardless of how good the code is.
`uvx sup` is that path in one command, and the lockfile is what makes it
reproducible rather than hopeful.

Accepted costs:

- Install instructions lead with the `uv` one-liner rather than generic
  Python packaging steps.
- Dependency constraints still need a call — exact pins versus ranges —
  before the first release.
- Hand-rolled pieces ([ADR-0001](0001-language-and-dependency-philosophy.md))
  must build cleanly with no separate build step or compiled artifact outside
  what `pyproject.toml` handles.
- Anything requiring credentials to obtain is out of scope, since it would
  put a step between the one-liner and a working run.
