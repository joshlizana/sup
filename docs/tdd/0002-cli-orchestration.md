# TDD-0002: CLI process orchestration

## Summary

Breaks down the work ADR-0006 committed to (bare `sup` supervising ingest,
mart-transform, and the dashboard as separate processes, with the TUI in the
main process) into concrete, scopable pieces. Also resolves the
subprocess-output open question TDD-0001 left dangling, incorporates the
control-plane/
single-instance-lock design from [ADR-0007](../adr/0007-control-plane-ipc.md),
and surfaces one prerequisite this exposes that actually belongs earlier
than orchestration itself.

## Goals / Non-goals

**Goals:**
- Enough process supervision to make bare `sup` reliable: correct startup
  ordering, detectable failure, clean shutdown with no orphaned processes.

**Non-goals (deferred to M5 or later):**
- Auto-restart / crash-loop backoff for a subprocess that dies unexpectedly.
  V1 policy is: surface the failure, don't try to recover automatically —
  that's real added complexity the thin slice doesn't need yet.
- Log tailing/streaming into the TUI. Log files exist for post-hoc
  debugging; the TUI's live status comes from querying the raw store/mart
  directly (per TDD-0001 [4a]).

## Prerequisite (belongs in M1): shared data directory

Where do the raw SQLite store, the DuckLake catalog, and the DuckLake data
files actually live on disk? Every subprocess orchestration spawns needs to
agree on this, which is what makes it surface here — but the need is real
as soon as M1 exists, independent of orchestration.

**Proposed:** a single resolvable data directory (e.g. via the `platformdirs`
library for a cross-platform, XDG-compliant path — `user_data_dir("sup")`),
with raw-store/catalog/mart paths derived from it. Small enough to flag here
directly for confirmation, rather
than deciding it silently — say if you'd rather do something else.

## Design

### 1. Process spawn

The orchestrator (bare `sup`) launches `sup ingest`, `sup transform`, and
`sup dashboard` as subprocesses of itself — re-invoking its own entry point
rather than duplicating their logic in-process. The TUI runs directly in the
main process (it needs the terminal, and per ADR-0006 isn't a subprocess).

### 2. Control plane & single-instance lock

`sup ingest` and `sup transform` each bind a control-plane channel on
startup — via stdlib `multiprocessing.connection` (Unix domain socket /
Windows named pipe, `authkey`-authenticated; not TCP) — at a fixed path
inside the shared data directory (e.g. `<data-dir>/control/ingest.sock`).
This happens whether the process was launched standalone or by the
orchestrator. See [ADR-0007](../adr/0007-control-plane-ipc.md) for why this
mechanism and not TCP loopback.

The fixed-path bind doubles as a single-instance lock, made race-free with a
paired `flock`'d PID file (`<data-dir>/control/ingest.lock`):

1. On startup, attempt to acquire the flock (non-blocking).
2. Success → no other live instance holds it. Bind the control socket,
   removing any stale socket file left behind by a prior unclean exit (safe
   now, since the flock proves nothing live is using it).
3. Failure → another live instance already holds the lock. Print a clear
   error and exit non-zero rather than silently no-op'ing or racing.

**Hardening, both required:** the socket file gets permissive default
permissions from the umask on bind (`0777` minus umask — commonly `0755`,
connectable by any local user) — `os.chmod(socket_path, 0o600)` right after
bind closes that gap. The `authkey` is generated once and persisted to
`<data-dir>/control/authkey`, created atomically at `0o600` via
`os.open(..., os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)` — persisted
because two standalone invocations need to agree on the same key with no
shared parent to hand one off. Together they close the permissions gap and
establish the shared secret; see
[ADR-0007](../adr/0007-control-plane-ipc.md) for the full reasoning
(including the CPython CVE this same class of gap caused elsewhere).

Applies to ingest and transform (the writers, where a second concurrent
instance would corrupt shared state). For the dashboard, a duplicate
instance is only a port-conflict nuisance, so it doesn't need this
protection.

This is additive to signal-based shutdown: both processes still respond to
SIGTERM/Ctrl+C with no orchestrator or control-plane client involved, so an
external `kill` is graceful and a hand-run subcommand exits cleanly. That
path is defensive — the orchestrator drives shutdown over the control plane
(see [ADR-0006](../adr/0006-cli-orchestration-model.md)).

### 3. Startup ordering / readiness

Start ingest, wait for its control socket to become connectable (the
readiness signal, reusing the mechanism from step 2), then start transform,
then the dashboard, then attach the TUI.

### 4. Liveness monitoring

Orchestrator periodically checks each subprocess's exit status. V1 policy:
on unexpected exit, surface it as an error state (visible in the TUI's
error/reconnect log, per TDD-0001 [4a]/M3) and stop — no auto-restart (see
Non-goals).

### 5. Graceful shutdown — order matters

Ctrl+C signals the whole foreground process group, so a default-spawned
child receives SIGINT at the same instant the orchestrator does — measured.
The three services are therefore spawned into their own process groups
(`start_new_session=True` on Unix, `CREATE_NEW_PROCESS_GROUP` on Windows) so
only the main process catches the terminal signal and can sequence what
follows. Windows cannot deliver SIGTERM at all, so the orchestrator drives
each step over the control plane rather than by signalling.

`ctrl+q` in the TUI triggers it, and the sequence is displayed as it runs —
each service's state changes in view before the app exits
([ADR-0015](../adr/0015-tui-as-control-plane-client.md)). Textual clears
`ISIG`, so `ctrl+c` stays a key event bound to Textual's default notification
rather than raising SIGINT. Teardown after `app.run()` returns catches the
paths no binding sees. An external SIGTERM to the main process runs the same
sequence.

The order:

1. Signal ingest first; wait for it to finish flushing its current batch and
   exit (ties to M1's batched writer needing its own graceful-stop hook).
2. Signal transform; wait for it to finish its current cycle, protecting
   watermark-state consistency.
3. Stop the dashboard — safe to terminate more abruptly, it's stateless/
   read-only.
4. TUI exits last.

This means `sup ingest` and `sup transform` each need their own signal
handler for graceful shutdown — two small, separate pieces of work. See §6
below for what "finish flushing its current batch" actually involves
inside `sup ingest` — it shares its core logic with pause.

### 6. Pause/resume and shutdown — shared quiesce

Pause and shutdown both arrive as control-plane commands on a service's
Listener (from the TUI, per [TDD-0001](0001-architecture-overview.md) [4a])
and share most of their work. **`sup ingest` and `sup transform` both
support them**, which is what lets `sup clean` pause both services before
wiping data ([ADR-0008](../adr/0008-sup-clean-full-reset.md)). Transform's
quiesce is its own equivalent of "disconnect + flush" — finish or don't
start a cycle — in the same shape.

Both route through `asyncio.run_coroutine_threadsafe()` from the listener
thread, since the real work (disconnecting the websocket, flushing the
current batch) is async and a polled flag cannot express it. The shared part
lives in one coroutine, `_quiesce()`.

For ingest, "disconnect + flush" is an ordered sequence: stop the range
workers first (each re-enqueues its narrowed range, per
[TDD-0003](0003-ingest-backfill-and-gap-recovery.md) §4), let the writer
drain both queues to empty, then stop the writer. Stopping producer and
consumer together strands whatever is queued.

- **Shutdown** = quiesce, close the DB connection, release the lock, exit.
- **Pause** = quiesce, then idle. The process, event loop, Listener and lock
  all stay live, so a resume command can reach it and a paused instance is
  still *the* running instance for this data directory.

**Resume reuses reconnect.** Pause disconnects the websocket, so resuming is
structurally an ordinary reconnect after a network drop, relying on
Jetstream's cursor-resume ([TDD-0001](0001-architecture-overview.md) [1]) to
avoid a gap.

**Commands can arrive before the workers are running.** Ingest binds its
control socket at acquisition, but its gap audit
([TDD-0003](0003-ingest-backfill-and-gap-recovery.md) §1) probes every
endpoint before any shard exists, leaving a startup window where the
service accepts commands while its worker tasks are still to come. Three
rules cover that window:

- Worker objects are constructed at acquisition, so quiesce and resume have
  something to iterate from the moment the socket is live.
- Worker *tasks* start after the audit, when the service is active and
  unpaused.
- Quiesce runs safely before any task exists, and the run loop drains again
  on its way out, so whichever runs first, the other catches the rest.

A shutdown during the audit therefore logs its drain sequence twice: once
against an empty set, once against the live workers.

**Resume creates the next generation of workers itself**, mirroring the way
quiesce stops and awaits them. A supervising loop would tie the restart to
its poll interval, where a pause and resume inside one interval leaves the
service running with no workers. Resume is idempotent: a second call against
an active service returns without starting a duplicate generation.

**Delivery mechanism.** Whatever triggers pause/shutdown (the TUI, or a
signal handler reacting to Ctrl+C) connects to the Listener as a client and
sends the command. This is what lets a blocked `listener.accept()`/`recv()`
actually be acted on: the command's arrival is itself the wakeup. The
listener thread checks/acts on the command between `recv()` and `send()`,
then bridges the real work into the event loop via `run_coroutine_threadsafe`.

**Resolved — acknowledgment blocks.** The reply is sent after the hook
returns, so a client's `recv()` returning confirms the requested state was
reached rather than merely requested.

### 7. Subprocess output/logging

**Proposed resolution** to TDD-0001's open question: each subprocess's
stdout/stderr redirected to its own log file under the shared data
directory (e.g. `<data-dir>/logs/ingest.log`), kept separate from the
terminal the TUI owns. Log files are for post-hoc debugging only.

### 8. Verification

- Exercise both a clean Ctrl+C and an unclean `kill -9` of the parent
  process; confirm no orphaned subprocesses either way. This is TODO.md's
  orchestration acceptance criterion restated as the concrete test procedure
  to actually run.
- Lock correctness: start `sup ingest` twice against the same data
  directory — the second must refuse cleanly with a clear error, not hang
  or silently no-op. Then `kill -9` a running `sup ingest`, and confirm the
  next launch detects the stale lock/socket correctly and starts normally,
  rather than refusing to start forever.
- Pause/resume: send a pause command, confirm the websocket actually
  disconnects and the write queue flushes (no stuck in-flight batch); send
  resume, confirm ingestion picks back up via the reconnect/cursor-resume
  path with no gap or duplication. Confirm the lock and control socket are
  still held/listening throughout — a paused instance shouldn't look "not
  running" to a second launch attempt or to the readiness check.

## Sequencing

This entire TDD depends on M1 (ingest), M2 (transform), M3 (TUI error/status
display), and M4 (dashboard subprocess launch, already partly designed in
ADR-0005's Consequences) existing individually first — it's a capstone. The
one exception is the shared data directory (Prerequisite above), which
should land as part of M1.

## Open questions

- Whether transform's "finish current cycle before stopping" needs a hard
  timeout in case a run hangs. Not yet bounded.
- **Resolved:** pause/shutdown acknowledgment blocks until the hook
  returns (§6).
- **Resolved:** transform gets the same pause/resume treatment as ingest —
  see §6 and [ADR-0008](../adr/0008-sup-clean-full-reset.md).
- **Resolved:** ingest holds no separate cursor state. Each worker's
  position lives in its in-memory range, and the durable position is
  whatever `events` contains — the every-start audit derives the rest.
  A wipe therefore leaves nothing stale behind, which is what ADR-0008's
  "resume-after-clean starts fresh" assumes.
- **Resolved:** the control plane carries a `status` command. Ingest reports
  rate, connection state, queue depth, and worker count; transform reports its
  committed watermark. The TUI polls both and holds no database connection
  ([ADR-0015](../adr/0015-tui-as-control-plane-client.md)).
