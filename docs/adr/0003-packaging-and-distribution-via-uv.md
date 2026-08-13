# ADR-0003: Packaging and distribution via uv

## Status

Accepted

## Context

The project's portfolio goal requires that a stranger (recruiter, reviewer)
can install and run `sup` reliably with minimal friction. This is as much a
constraint on packaging as on code quality — a broken or fiddly install
experience undermines the project regardless of how good the code is.

## Options considered

### Option A: `pip` + `requirements.txt` / plain `setup.py`

- Familiar, but slower dependency resolution and no lockfile story by
  default; more room for "works on my machine" drift between the author's
  environment and a stranger's.

### Option B: `uv` with `pyproject.toml`, installable via `uv tool install` / `uvx`

- Fast, reproducible resolution with a lockfile.
- `uvx sup` / `uv tool install sup` gives a one-command install-and-run path,
  which is exactly the low-friction experience the portfolio goal needs.
- Actively maintained, increasingly the default recommendation in the Python
  packaging ecosystem.

## Decision

**Option B.** Package with `pyproject.toml`, define a console-script entry
point, and target `uv` (`uv tool install` / `uvx`) as the primary, documented
install path. `pip install`-compatible packaging should still work as a
fallback since `pyproject.toml` isn't `uv`-exclusive, but `uv` is what gets
documented and tested against.

## Consequences

- README/install instructions should lead with the `uv` one-liner, not bury
  it under generic Python packaging instructions.
- Dependency version constraints need a real decision (exact pins vs. ranges)
  before the first release — pins maximize reproducibility for a stranger's
  install but require more maintenance; this is a candidate for a follow-up
  ADR if it turns out to be contentious, otherwise a TODO item is enough.
- Any hand-rolled pieces (per ADR-0001) still need to build cleanly as part of
  this packaging — no separate build step or compiled artifact outside what
  `uv`/`pyproject.toml` handles, to keep the install path uniform.
