# ADR-0006: CLI orchestration model (separate processes)

## Status

Accepted

## Context

Bare `sup` (no subcommand) is going to orchestrate launching ingest,
mart transform, the TUI, and the dashboard together, rather than requiring
each to be started separately. This is a real architecture decision, not
just CLI UX: it determines whether ingest and the mart transform end up
running as genuinely separate OS processes or as concurrent tasks sharing
one process (e.g. asyncio tasks alongside Textual's event loop, which
supports background `Worker`s cleanly).

This connects directly to [ADR-0002](0002-raw-ingestion-durability.md)'s
accepted risk: that ADR knowingly accepted *cross-process* SQLite/DuckDB
concurrency as unvalidated but plausible, specifically because the one
concurrency bug actually found in research
([duckdb-sqlite#82](https://github.com/duckdb/duckdb-sqlite/issues/82)) is a
*same-process* scenario — one connection writing to a SQLite file while
another reads it in the same process, causing crashes (`SIGBUS`) and lock
errors. An in-process orchestration model for ingest + transform would risk
walking directly into that more clearly-documented-as-broken scenario,
undermining the risk boundary ADR-0002 deliberately drew.

Streamlit's dashboard runs its own Tornado server regardless of this
decision, so it needs to be a subprocess either way — it isn't part of the
tradeoff.

## Options considered

### In-process: asyncio tasks + Textual workers

- Simpler single-process model for ingest, transform, and the TUI.
- Textual's `Worker` system supports async/threaded background tasks
  cleanly without blocking the UI event loop.
- Risks the documented same-process SQLite/DuckDB concurrency bug for the
  ingest↔transform relationship specifically.

### Separate OS processes, `sup` as supervisor

- `sup` (bare) spawns and supervises subprocesses for ingest, the mart
  transform loop, and the dashboard; the TUI runs in the main process,
  attached to the terminal.
- Keeps ingest and the mart transform in genuinely separate processes,
  matching the scenario ADR-0002 actually accepted the risk for, not the
  worse one.
- More implementation work: process lifecycle (start, monitor, clean
  shutdown/signal propagation), and a plan for multiple subprocesses'
  output (raw interleaved stdout from four processes would be unusable).

## Decision

**Separate OS processes.** Bare `sup` supervises subprocesses for ingest,
the mart-transform loop, and the Streamlit dashboard; the TUI runs in the
main process attached to the terminal, and is the control surface every
command is issued from. Individual subcommands (`sup ingest`,
`sup transform`, `sup tui`, `sup dashboard`) are how the orchestrator spawns
each service — it re-invokes its own entry point rather than duplicating
service logic in-process. They stay usable by hand for development, without
being a supported product surface.

Bare `sup` is the only run mode: TUI and dashboard always launch alongside
ingest and transform. A headless mode was considered and rejected — the
charter scopes this to a single-operator local tool, self-daemonizing
duplicates what systemd already does, and anyone who does not want to look
at the dashboard can close the browser.

Chosen specifically because it keeps ADR-0002's accepted risk boundary
intact (cross-process concurrency, which is unvalidated but not the
documented-broken case) rather than accidentally trading it for a worse,
better-documented one for the sake of CLI simplicity.

## Consequences

- Process-lifecycle management becomes real, necessary work: starting each
  subprocess, monitoring liveness, and propagating shutdown (Ctrl+C on the
  TUI/main process should cleanly stop the ingest, transform, and dashboard
  subprocesses, not orphan them). Should be tested explicitly — e.g. kill
  the parent and verify children don't leak.
- Subprocess output needs a plan (log files, or only surfaced through
  TUI/dashboard state) — not yet resolved, see Open Questions in
  [TDD-0001](../tdd/0001-architecture-overview.md).
- Slightly more implementation work up front than an in-process model, in
  exchange for not accidentally compounding ADR-0002's already-accepted
  concurrency risk.
- **The TUI keeping the terminal is what makes this cheap.** As a fourth
  subprocess it would share one tty with its parent: the orchestrator could
  never write to stdout or stderr again, `start_new_session` would cost it
  SIGWINCH, and a hard-killed TUI would leave the parent's terminal in raw
  mode. In the main process, Textual owns the terminal and restores it, and
  supervision runs as Textual `Worker`s on the same loop.
- **Ctrl+C reaches every child in the process group.** Measured: a
  default-spawned child receives SIGINT at the same instant as the parent,
  while `start_new_session=True` isolates it. Ordered shutdown therefore
  requires spawning the three services into their own process groups so only
  the main process catches the terminal signal — see
  [TDD-0002](../tdd/0002-cli-orchestration.md) §5.
- Standalone subcommands stay runnable but stop being a design constraint.
  Each service still handles SIGTERM so an external `kill` is graceful, which
  is defensive rather than load-bearing now that the orchestrator drives
  shutdown over the control plane.
