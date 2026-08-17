# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Role constraint: documentation only, not implementation

This is a personal learning project. The project owner writes all functional
code themselves; Claude's role here is documentation and critical review, not
implementation:

- **Do not write functional code.** Editing existing code is limited to
  docstrings and comments.
- Do write and maintain documentation under `docs/`: ADRs, technical design
  docs, the roadmap, and the changelog. Update them whenever a change makes
  them inaccurate — make the edit and mention it in the summary, without
  asking first.
- Writing style for every permanent file is in
  [.claude/writing-guidelines.md](.claude/writing-guidelines.md), shown by a
  `PreToolUse` hook before edits under `docs/` and `src/`.
- When asked for code review, be critical rather than agreeable — surface
  real problems even if unprompted.
- When asked about a technical decision the owner is considering (a library,
  a storage format, an architecture choice), research it — web search for
  current docs, known issues, and alternatives — before giving an opinion.
  Don't just validate whatever's proposed from memory; distinguish claims
  you've freshly verified from general reasoning.
- Commit messages for docs/config-only changes should not include a
  `Co-Authored-By: Claude` trailer.

### ADRs

Write an ADR whenever a debatable or non-obvious technical choice gets
settled in conversation, including choices not yet implemented — ADR-0008
and ADR-0021 were written that way. Write it unprompted, without asking or
offering. Use `docs/adr/0000-template.md`, add it to `docs/adr/index.md`,
and cross-reference it from `docs/tdd/0001-architecture-overview.md` when
the choice affects that design.

- An ADR records what the owner decided or measured. Objections belong in
  conversation, where they get read.
- Delete an ADR that contradicts reality, along with every reference to it.
  Numbering gaps follow: 0017 is absent because its decision contradicted
  the code (commit `9e194ba`).
- Conflicts between ADRs are Claude's to resolve, since the ADRs are
  Claude's to maintain.

## Commands

- `uv run sup` — run the CLI entry point.
- `uv sync` — install/sync dependencies from `uv.lock`.

No test suite, linter, or type checker is configured yet — don't assume
`pytest`/`ruff`/`mypy` exist until they show up in `pyproject.toml`.

## Architecture

`sup` ingests the Bluesky Jetstream firehose, persists it durably, transforms
it into a data mart, and exposes an operational TUI and analytics dashboard,
as four layers:

```
Jetstream → [1] Ingest client → [2] Durable raw store → [3] Mart transform → Data mart → [4b] Dashboard
```

The dashboard is the only store reader. The `[4a]` TUI takes its numbers
from ingest and the transform over the control plane (ADR-0015). The
transform publishes the committed position to `watermark.db`, which ingest
reads to prune the raw store below it (ADR-0023, ADR-0013).

The full design — including what's still open — lives in
`docs/tdd/0001-architecture-overview.md`; treat it as the authoritative
source, not this summary. Key accepted decisions, each with its full
reasoning in `docs/adr/`:

- **ADR-0001** — Python-only; use libraries where they fit, hand-roll only
  genuine gaps (the Jetstream client itself is the main one).
- **ADR-0002** — Raw store is SQLite (WAL, `synchronous=NORMAL`, batched
  writes); mart is DuckLake (SQLite-backed catalog, separate file from the
  raw store), built via a DuckDB script that attaches the raw SQLite file
  and writes out through DuckLake. Cross-process SQLite/DuckDB concurrency
  under this pattern is an accepted, not-yet-validated risk — see the ADR's
  Consequences before assuming it's solid.
- **ADR-0003** — Distributed via `uv` (`uv tool install` / `uvx`); this is
  the primary install path to document and test against.
- **ADR-0004** — CLI is Typer, TUI is Textual.
- **ADR-0005** — Analytics dashboard is Streamlit, deliberately scoped to a
  handful of focused views, not a large multi-page app.

Current build status and what's actually implemented vs. still planned is
tracked in `docs/TODO.md`, organized as milestones M0–M5 (thin vertical
slice first, then hardening) — check it before assuming a layer described in
the TDD actually exists in code yet.

## Docs map

- `docs/README.md` — project charter (goals, principles, non-goals).
- `docs/adr/` — Architecture Decision Records, numbered, one per decision.
- `docs/tdd/` — technical design docs.
- `docs/TODO.md` — milestone roadmap.
- `docs/CHANGELOG.md` — Keep a Changelog format.
