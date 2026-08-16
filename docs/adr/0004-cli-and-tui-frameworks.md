# ADR-0004: CLI framework (Typer) and TUI framework (Textual)

## Status

Accepted

## Options considered

**CLI**

- **Typer.** Built on Click, type-hint-driven, widely adopted and
  immediately recognizable. No `Union` support in parameters, and its
  proxy-default-value design makes command functions awkward to call outside
  Typer's own invocation path — testing friction rather than a blocker.
- **Cyclopts.** Positioned as a Typer improvement: shorter code,
  docstring-driven help, `Union`/`Literal` parameters with automatic
  validation. Much smaller community for a problem Typer already solves.
- **Click / argparse.** Click is the mature foundation Typer sits on, but
  more verbose, with decorators and parameter declarations unsynchronized.
  `argparse` is stdlib and dependency-free but has no type-hint integration
  and more boilerplate for a subcommand-heavy CLI.

**TUI**

- **Textual.** The most capable Python TUI framework: reactive component
  model, CSS-like styling, async event loop, and published engineering work
  on high-performance rendering for live-updating terminal apps, which is
  directly relevant to the ingest-rate and connection-status widgets.
  Textualize, Inc. shut down in May 2025, leaving it effectively a
  single-maintainer project
  ([the future of Textualize](https://textual.textualize.io/blog/2025/05/07/the-future-of-textualize/)),
  though v4.0 shipped that July
  ([Textual v4.0](https://simonwillison.net/2025/Jul/22/textual-v4/)).
- **urwid, prompt_toolkit, hand-rolled curses.** All lower-level, so more of
  the TUI itself gets hand-rolled against
  [ADR-0001](0001-language-and-dependency-philosophy.md)'s reasoning for a
  solved problem. None avoid a governance tradeoff of their own — urwid is
  less actively developed, and prompt_toolkit is built for prompting rather
  than full-screen apps.

## Decision

**Typer for the CLI, Textual for the TUI.**

## Why

Typer over Cyclopts is about risk-stacking rather than merit. `sup` already
makes one real bet on newer tooling in
[ADR-0002](0002-raw-ingestion-durability.md)'s DuckLake choice, and Typer's
maturity complements that better than a second smaller-community pick
compounds it. The `Union` limitation blocking a real CLI need is the trigger
to revisit, not a reason to switch pre-emptively.

Textual is accepted despite the single-maintainer risk because the
capability gap over every alternative is large enough that the alternative
is reinventing a solved problem, and the project has kept shipping
meaningful releases since the company wound down.

Accepted costs:

- Textual's version gets pinned deliberately rather than tracking latest. A
  dependency with reduced maintenance capacity is not where to absorb
  upstream churn unreviewed.
- If Textual's maintenance visibly stalls before the TUI milestone (M3),
  this decision gets re-checked against project activity at that point
  rather than against this snapshot.
- The CLI's help text and structure are part of the portfolio surface, being
  a reviewer's first interaction with the tool.
