# ADR-0006: CLI orchestration model (separate processes)

## Status

Accepted

## Options considered

- **In-process: asyncio tasks and Textual workers.** A simpler single-process
  model for ingest, transform and the TUI, and Textual's `Worker` system
  handles async or threaded background work without blocking the UI event
  loop. Puts ingest and transform in one process, which is the
  documented-broken SQLite/DuckDB case.
- **Separate OS processes, `sup` as supervisor.** Keeps ingest and the mart
  transform genuinely separate, at the cost of real process-lifecycle work
  and a plan for four processes' output. Chosen.

Streamlit runs its own Tornado server regardless, so the dashboard is a
subprocess either way and is not part of the tradeoff.

## Decision

Bare `sup` supervises subprocesses for ingest, the mart-transform loop, and
the Streamlit dashboard. The TUI runs in the main process attached to the
terminal, and is the control surface every command is issued from. The
orchestrator spawns each service by re-invoking its own entry point —
`sup ingest`, `sup transform`, `sup tui`, `sup dashboard` — rather than
duplicating service logic in-process. Those subcommands stay usable by hand
for development without being a supported product surface.

Bare `sup` is the only run mode: TUI and dashboard always launch alongside
ingest and transform. A headless mode is rejected — the charter scopes this
to a single-operator local tool, self-daemonizing duplicates what systemd
already does, and anyone who does not want to look at the dashboard can
close the browser.

## Why

[ADR-0002](0002-raw-ingestion-durability.md) accepted cross-process
SQLite/DuckDB concurrency as unvalidated but plausible, specifically because
the one concurrency bug found in research
([duckdb-sqlite#82](https://github.com/duckdb/duckdb-sqlite/issues/82)) is a
*same-process* scenario: one connection writing to a SQLite file while
another reads it in the same process, causing `SIGBUS` and lock errors. An
in-process model for ingest and transform walks directly into that
better-documented failure. Separate processes keep the risk boundary where
ADR-0002 drew it instead of trading it for a worse one to simplify the CLI.

The TUI keeping the terminal is what makes supervision cheap. As a fourth
subprocess it would share one tty with its parent: the orchestrator could
never write to stdout or stderr again, `start_new_session` would cost it
SIGWINCH, and a hard-killed TUI would leave the parent's terminal in raw
mode. In the main process, Textual owns the terminal and restores it, and
supervision runs as Textual `Worker`s on the same loop.

Accepted costs:

- Process lifecycle becomes real work: starting each subprocess, monitoring
  liveness, and propagating shutdown so Ctrl+C on the main process stops the
  children rather than orphaning them. Worth an explicit test that kills the
  parent and checks for leaks.
- **Ctrl+C reaches every child in the process group.** Measured: a
  default-spawned child receives SIGINT at the same instant as the parent,
  while `start_new_session=True` isolates it. Ordered shutdown therefore
  spawns the three services into their own process groups so only the main
  process catches the terminal signal —
  [TDD-0002](../tdd/0002-cli-orchestration.md) §5.
- Subprocess output needs a plan, either log files or surfacing only through
  TUI and dashboard state. Unresolved; see Open Questions in
  [TDD-0001](../tdd/0001-architecture-overview.md).
- Each service still handles SIGTERM so an external `kill` is graceful.
  Defensive rather than load-bearing, now that the orchestrator drives
  shutdown over the control plane.
