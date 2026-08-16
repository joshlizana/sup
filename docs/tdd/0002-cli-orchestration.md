# TDD-0002: CLI process orchestration

## Summary

Breaks [ADR-0006](../adr/0006-cli-orchestration-model.md) — bare `sup`
supervising ingest, transform and the dashboard as separate processes with
the TUI in the main process — into scopable pieces. Incorporates the
control-plane and single-instance-lock design from
[ADR-0007](../adr/0007-control-plane-ipc.md), and resolves the
subprocess-output question TDD-0001 left open.

## Goals / Non-goals

**Goal:** enough supervision to make bare `sup` reliable — correct startup
ordering, detectable failure, clean shutdown with no orphaned processes.

**Non-goals (M5 or later):**

- Auto-restart or crash-loop backoff. V1 surfaces the failure instead.
- Log tailing into the TUI. Log files are for post-hoc debugging; live status
  comes over the control plane
  ([ADR-0015](../adr/0015-tui-as-control-plane-client.md)).

## Prerequisite (M1): shared data directory

Every spawned subprocess must agree on where the raw store, catalog and mart
files live. Resolved: one `platformdirs` root with paths derived from it,
implemented as `Config.data_path` and `Config.control_path`.

## Design

### 1. Process spawn

Bare `sup` launches `sup ingest`, `sup transform` and `sup dashboard` as
subprocesses, re-invoking its own entry point rather than duplicating service
logic. The TUI runs in the main process, which is where the terminal is.

### 2. Control plane and single-instance lock

Each service binds a control channel on startup via
`multiprocessing.connection` — Unix domain socket or Windows named pipe,
authkey-authenticated — at a fixed path under the shared data directory, and
does so whether launched standalone or by the orchestrator.

The fixed-path bind doubles as a single-instance lock, made race-free by a
paired `flock`'d PID file:

1. Acquire the flock non-blocking.
2. Success means no other live instance holds it. Bind the socket, removing
   any stale file left by an unclean exit — safe because the flock proves
   nothing live is using it.
3. Failure means another instance is running. Print a clear error and exit
   non-zero.

**Two hardening steps, both required.** Bind gives the socket umask-derived
permissions, commonly `0755` and connectable by any local user, so
`os.chmod(socket_path, 0o600)` follows it. The authkey is generated once and
persisted at `0o600` via `os.open(..., O_CREAT | O_WRONLY | O_EXCL)`, because
two standalone invocations have no shared parent to hand one off. See
[ADR-0007](../adr/0007-control-plane-ipc.md).

Applies to ingest and transform, where a second instance would corrupt shared
state. A duplicate dashboard is only a port conflict.

Both services also respond to SIGTERM with no orchestrator involved, so an
external `kill` is graceful. That path is defensive — the orchestrator drives
shutdown over the control plane.

### 3. Startup ordering

Start ingest, wait for its control socket to become connectable, then
transform, then the dashboard, then attach the TUI.

### 4. Liveness monitoring

The orchestrator polls each subprocess's exit status. On unexpected exit it
surfaces an error state in the TUI and stops — no auto-restart.

### 5. Graceful shutdown — order matters

Ctrl+C signals the whole foreground process group, so a default-spawned child
receives SIGINT at the same instant as the parent — measured. The three
services are therefore spawned into their own process groups
(`start_new_session=True` on Unix, `CREATE_NEW_PROCESS_GROUP` on Windows) so
only the main process catches the terminal signal. Windows cannot deliver
SIGTERM at all, so the orchestrator drives each step over the control plane
rather than by signalling.

`ctrl+q` in the TUI triggers it, and the sequence is displayed as it runs
([ADR-0015](../adr/0015-tui-as-control-plane-client.md)). Textual clears
`ISIG`, so `ctrl+c` stays a key event bound to Textual's default notification.
Teardown after `app.run()` returns catches the paths no binding sees. An
external SIGTERM runs the same sequence.

The order:

1. Ingest first — finish flushing the current batch and exit.
2. Transform — finish the current cycle, protecting watermark consistency.
3. Dashboard — stateless and read-only, so it can be terminated abruptly.
4. TUI exits last.

### 6. Pause/resume and shutdown — shared quiesce

Pause and shutdown arrive as control-plane commands and share most of their
work, so the shared part lives in one `_quiesce()` coroutine. **Both services
support them**, which is what lets `sup clean` pause both before wiping
([ADR-0008](../adr/0008-sup-clean-full-reset.md)). Transform's quiesce is
"finish or don't start a cycle".

Both route through `asyncio.run_coroutine_threadsafe()` from the listener
thread, since disconnecting a websocket and flushing a batch are async and a
polled flag cannot express them.

For ingest the sequence is ordered: stop the range workers first, each
re-enqueueing its narrowed range
([TDD-0003](0003-ingest-backfill-and-gap-recovery.md) §4), let the writer
drain both queues, then stop the writer. Stopping producer and consumer
together strands whatever is queued.

- **Shutdown** = quiesce, close the DB connection, release the lock, exit.
- **Pause** = quiesce, then idle. The process, event loop, Listener and lock
  stay live, so a resume command can reach it and a paused instance is still
  *the* running instance for this data directory.

**Resume reuses reconnect.** Pause disconnects the websocket, so resuming is
structurally an ordinary reconnect after a network drop.

**Commands can arrive before the workers are running.** Ingest binds its
socket at acquisition, but the gap audit probes every endpoint before any
shard exists. Three rules cover that window:

- Worker objects are constructed at acquisition, so quiesce and resume have
  something to iterate from the moment the socket is live.
- Worker *tasks* start after the audit, when the service is active and
  unpaused.
- Quiesce runs safely before any task exists, and the run loop drains again on
  its way out, so whichever runs first, the other catches the rest.

A shutdown during the audit therefore logs its drain sequence twice.

**Resume creates the next generation of workers itself**, mirroring how
quiesce stops them. A supervising loop would tie the restart to its poll
interval, where a pause and resume inside one interval leaves the service
running with no workers. Resume is idempotent.

**Delivery mechanism.** Whatever triggers a command connects to the Listener
as a client and sends it, so the command's arrival is itself the wakeup for a
blocked `accept()`/`recv()`. The listener thread acts between `recv()` and
`send()`, then bridges the real work into the event loop.

**Acknowledgment blocks.** The reply is sent after the hook returns, so a
client's `recv()` returning confirms the state was reached rather than
requested.

### 7. Subprocess output

Each subprocess's stdout and stderr redirect to its own log file under the
shared data directory, kept separate from the terminal the TUI owns. For
post-hoc debugging only.

### 8. Verification

- Exercise a clean Ctrl+C and an unclean `kill -9` of the parent; confirm no
  orphaned subprocesses either way.
- Lock correctness: start `sup ingest` twice against the same data directory —
  the second must refuse cleanly. Then `kill -9` a running instance and
  confirm the next launch detects the stale lock and socket and starts
  normally.
- Pause/resume: confirm the websocket disconnects and the queue flushes, then
  that resume picks up with no gap or duplication. Confirm the lock and socket
  stay held throughout, so a paused instance does not look absent to a second
  launch or to the readiness check.

## Sequencing

This TDD depends on M1–M4 existing individually — it is a capstone. The one
exception is the shared data directory, which landed in M1.

## Open questions

- Whether transform's "finish current cycle" needs a hard timeout against a
  hung run. Not yet bounded.
- **Resolved:** acknowledgment blocks until the hook returns (§6).
- **Resolved:** transform gets the same pause/resume treatment as ingest (§6).
- **Resolved:** ingest holds no separate cursor state. Each worker's position
  lives in its in-memory range and the durable position is whatever `events`
  contains, so a wipe leaves nothing stale behind — which is what
  [ADR-0008](../adr/0008-sup-clean-full-reset.md) assumes.
- **Resolved:** the control plane carries a `status` command; the TUI polls it
  and holds no database connection
  ([ADR-0015](../adr/0015-tui-as-control-plane-client.md)).
