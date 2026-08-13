# ADR-0004: CLI framework (Typer) and TUI framework (Textual)

## Status

Accepted

## Context

`sup` needs a CLI entry point (subcommands for ingest, transform, TUI,
dashboard, etc. — see [TODO.md](../TODO.md)) and an operational TUI (per
[TDD-0001](../tdd/0001-architecture-overview.md) [4a]) showing live ingest
status. Per [ADR-0001](0001-language-and-dependency-philosophy.md), both are
solved problems that should lean on libraries rather than be hand-rolled.

TDD-0001 explicitly left "TUI/dashboard framework choices... not yet formally
chosen" as an open question. This ADR resolves the CLI/TUI half of it.

## Options considered

### CLI: Typer

- Built on Click; type-hint-driven command definitions; large adoption,
  immediately recognizable to Python-familiar reviewers (same lineage as
  FastAPI, a good aesthetic match for the project's other modern-typed-Python
  choices).
- Known limitation: no support for `Union` types in parameters, and its
  decorator/proxy-default-value design makes command functions somewhat
  awkward to call directly outside Typer's own invocation path (mostly a
  testing-style friction, not a functional blocker).

### CLI: Cyclopts

- Newer library explicitly positioned as a Typer improvement: shorter code,
  docstring-driven help text, and support for `Union`/`Literal` parameter
  types with automatic validation.
- Smaller community and adoption than Typer; effectively a bet on a less
  battle-tested library for a problem Typer already solves adequately.

### CLI: Click / argparse

- Click: the mature foundation Typer is built on, but more verbose/explicit
  (decorator + parameter declarations not synchronized via type hints).
- `argparse`: stdlib, zero dependency, but no type-hint integration and more
  boilerplate for a subcommand-heavy CLI with polished help text.
- Both are safe but give up the ergonomics/readability Typer offers for a
  portfolio piece where the CLI itself is part of what's being shown off.

### TUI: Textual

- By far the most capable Python TUI framework available: reactive
  component model, CSS-like styling, async event loop, and published
  engineering work specifically on high-performance rendering for
  live-updating terminal apps — directly relevant to sup's ingest-rate and
  connection-status widgets.
- **Governance risk:** Textualize, Inc. (the company behind Textual and
  Rich) shut down in May 2025. Founder Will McGugan stated the company
  "struggled to identify a viable business model" but committed to
  personally continuing to maintain both projects as open source. That
  commitment has held up in practice — Textual shipped v4.0 (the "Streaming
  Release") in July 2025, after the shutdown announcement — but this is now
  effectively a single-maintainer project rather than a funded team, which
  is a real bus-factor risk for a tool meant to install and run reliably for
  strangers.
- See [The future of Textualize](https://textual.textualize.io/blog/2025/05/07/the-future-of-textualize/)
  and [Textual v4.0](https://simonwillison.net/2025/Jul/22/textual-v4/).

### TUI: alternatives (urwid, prompt_toolkit, hand-rolled curses)

- All lower-level than Textual, meaning more of the TUI itself would need to
  be hand-rolled — directly against ADR-0001's reasoning for a solved
  problem. None avoid a governance-risk tradeoff of their own (urwid is
  older and less actively developed; prompt_toolkit is maintained but
  designed more for input/prompting than full-screen apps).

## Decision

**Typer for the CLI, Textual for the TUI.**

Typer over Cyclopts specifically because of risk-stacking: `sup` is already
making one real bet on newer, less-proven tooling
([ADR-0002](0002-raw-ingestion-durability.md)'s DuckLake choice). Typer's
maturity and ubiquity is a better complement to that than compounding it with
a second smaller-community pick. If Typer's `Union`-type limitation turns out
to block a real CLI need, that's the trigger to revisit — not a reason to
pre-emptively switch.

Textual accepted despite the post-shutdown single-maintainer risk, because
the capability gap versus every alternative is large enough that hand-rolling
would mean reinventing a solved problem, and the project has continued
shipping meaningful releases since the company wound down.

## Consequences

- Textual's version should be pinned deliberately rather than tracking
  latest automatically — a reduced-maintenance-capacity dependency is not
  the place to absorb upstream churn without review.
- If Textual's maintenance visibly stalls (no releases, unaddressed critical
  bugs) before `sup` reaches the TUI milestone (M3), this decision should be
  revisited rather than assumed still valid — check current project activity
  at that point, don't rely on this ADR's snapshot.
- The CLI's help text and structure become part of the portfolio surface
  (a reviewer's first interaction with the tool) — Typer's type-hint-driven
  approach should be used to keep that polished, not just functional.
- Deferred, not blocked: the analytics dashboard's UI framework is a separate
  open question (TDD-0001), not resolved by this ADR — it's a browser-facing
  concern, not a TUI one.
