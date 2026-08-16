# ADR-0015: The TUI is a control-plane client

## Status

Accepted

## Options considered

- **Derive status from the stores.** The TUI computes rate from recent
  `time_us` in the raw store and lag from the mart watermark, needing no new
  control-plane surface and keeping the TUI a pure reader. But connection
  state, worker generation, and reconnect history are not in the data at any
  grain, and every poll is a query against a file being written.
- **Status over the control plane.** A `status` command alongside pause,
  resume and shutdown, each service answering with its own view and the TUI
  polling. Authoritative, since each service reports on itself, and no
  database access from the TUI; costs a response schema to define, and status
  is unavailable exactly when a service is down. Chosen.

## Decision

**Status over the control plane, for every service.** Ingest reports rate,
connection state, queue depth, and worker count; the transform reports its
committed watermark. The TUI holds no SQLite or DuckDB connection.

**Shutdown is a displayed sequence.** `ctrl+q` runs the ordered shutdown from
[TDD-0002](../tdd/0002-cli-orchestration.md) §5 with each service's state
visible as it goes, then exits. `ctrl+c` keeps Textual's default, which
notifies that `ctrl+q` quits.

## Why

The earlier design had the TUI reading the raw store directly for
ingest-side metrics and summing `Reader.update_throughput()` across
`Ingester.readers`. Both predate
[ADR-0006](0006-cli-orchestration-model.md), which puts ingest in a
subprocess and the TUI in the main process — `Ingester.readers` lives in
another process and cannot be summed from the TUI at all.

[ADR-0013](0013-service-owned-pruning.md) removes the fallback. Once the raw
store is a working buffer, its row count measures backlog rather than
ingestion, and deriving a rate from it means querying the file ingest is
writing. The control plane already carries pause, resume and shutdown
([ADR-0007](0007-control-plane-ipc.md)), so status is an addition to an
existing surface rather than a new one.

Accepted costs:

- **The mart-only rule is restored.** The dashboard is the sole store reader,
  so [TDD-0001](../tdd/0001-architecture-overview.md)'s Goals need no
  exception for the TUI.
- **ADR-0002's concurrency risk shrinks to one pair.** The unvalidated
  cross-process case is now transform-writes and dashboard-reads. The TUI,
  which would have been a third participant polling continuously, is out of
  it entirely.
- **An unreachable socket is itself the status.** A stopped service shows as
  unreachable rather than as stale numbers, which is the same signal
  liveness monitoring needs (TDD-0002 §4). One per-service state model —
  running, stopping, stopped, unreachable — serves the status display and the
  shutdown display alike.
- **Shutdown runs off the UI thread.** Acknowledgment blocks until the hook
  returns (TDD-0002 §6) and ingest's drain takes real time, so the sequence
  runs as a Textual worker updating per-service state. Running it inline
  freezes the display.
- **Teardown after `app.run()` returns is the backstop.** It covers the paths
  no binding sees — a crash in the event loop, or `App.exit()` called
  elsewhere — and terminates whatever the displayed sequence left running.
- Textual clears `ISIG` unless `TEXTUAL_ALLOW_SIGNALS` is set, so Ctrl+C
  stays a key event while the TUI runs. The main process's signal handler
  serves external `kill`, and the services keep their own for the standalone
  case ([ADR-0006](0006-cli-orchestration-model.md)).
- The status payload stays a plain dict. Every process is released together,
  so a negotiated schema would version against itself.
