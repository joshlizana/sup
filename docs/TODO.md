# TODO / Roadmap

Organized as a thin vertical slice: a minimal version of every layer working
end-to-end (M0–M4) before deepening any one layer (M5+). See
[tdd/0001-architecture-overview.md](tdd/0001-architecture-overview.md) for the
layer breakdown.

No firm deadline — sequence and depth are driven by technical soundness and
learning value.

## M0: Project scaffold ✅

- [x] `pyproject.toml` with metadata and console-script entry point
- [x] `uv` lockfile committed, `uv run sup` works from a clean checkout
- [x] README install instructions (`uvx sup` / `uv tool install`)

**Acceptance:** a clean checkout installs and produces `sup --help` via the
documented `uv` command, with no manual steps.

## M1: Minimal ingest + durable raw store

- [x] Shared data directory: `Config` exposes `data_path` and `control_path`
      as computed sub-paths of one `platformdirs` root
- [x] `JetstreamClient`: connect with retry/backoff at a given URL and cursor,
      receive, close. Shared by shard workers, the live tail, and the
      retention probe
- [x] Collection filter as repeated `wantedCollections` query params
- [x] `Reader`: reads a `(start, end)` `time_us` range. The live tail is the
      same worker with `end = sys.maxsize` (TDD-0003 §4, §5)
- [x] `Reader.update_throughput()`: 10s rolling window per worker
- [x] Per-instance log identity via `sup.util.register()`
- [x] Gap detection on every start: `GapAuditor` mirrors `(pk, time_us)` into
      a DuckDB `gap_index` and scans it with a windowed `LAG` (TDD-0003 §1)
- [x] Retention-floor probe across all endpoints concurrently, with the
      configured value as a cap, tolerating partial failure (TDD-0003 §1)
- [x] Backfill sharding into 30-min ranges with a 10s overlap, live tail
      appended and claimed last (TDD-0003 §2, §5)
- [x] Range workers re-enqueue a narrowed range on interruption (TDD-0003 §4)
- [x] Priority work queue of ranges, oldest-first; FIFO message queues into
      the writer (TDD-0003 §3)
- [x] SQLite raw store: WAL, `synchronous=NORMAL`
- [x] Batched writer draining both queues via `executemany`, its batch capped
      by the queues' `maxsize=10,000`
      ([ADR-0009](adr/0009-bounded-write-queues.md))
- [x] Append-only raw store, deduplicated by the mart
      ([ADR-0010](adr/0010-deduplication-in-the-mart.md)), with `orjson`
      replacing the stdlib parser
- [x] Control-plane socket + `flock` single-instance lock, generic across
      services: `SupListener`/`SupClient` plus `Controller`
      ([ADR-0007](adr/0007-control-plane-ipc.md),
      [TDD-0002](tdd/0002-cli-orchestration.md) §2)
- [x] `Controller` dispatch: pause/resume/shutdown bridged into the event loop
      via `run_coroutine_threadsafe`, with a self-connect to wake a blocked
      `accept()` (TDD-0002 §6)
- [x] Ingest's `quiesce()`/`resume()`/`shutdown()`. `drain()` carries the
      ordering: readers stop and are awaited first, then the writer commits
      what they left (TDD-0002 §6)
- [x] `Ingester`: acquires the lock, runs the audit, seeds the queue,
      constructs workers, idles until shutdown
- [x] Graceful-shutdown signal handler, multiplatform via `signal.signal` plus
      `call_soon_threadsafe`. `_stopping` latches so a second signal or a
      draining pause cannot run the hook twice; `__aexit__` restores handlers
      in a `finally` after the listener is released
- [x] Messages missing a required column are dropped rather than sent to the
      DLQ — 0.16% of live traffic, all `account` and `identity` events
      (TDD-0003 §4)
- [x] `sup ingest` subcommand on the Typer app. Last piece before M1 runs from
      the entry point
- [x] Defend the live tail against cursor clamping
      ([ADR-0020](adr/0020-live-tail-cursor-clamping.md)), the cause of the
      46.19% duplicate share: `current_cursor` only advances, and a message
      outside the claimed range ends the connection rather than being stored
- [x] Readers claim no work when their endpoint's witness lag exceeds ten
      seconds, measured during the retention probe they already run
      ([ADR-0019](adr/0019-endpoint-witness-lag-screening.md))
- [ ] Guard `tid_us` against implausible values — 0.2351% of rows decode
      outside 2020-2030 from well-formed TIDs, 71,843 rows across 8,474 DIDs
      on a 27M-row store ([ADR-0018](adr/0018-event-time-from-commit-rev.md)).
      Wanted before the mart uses it as a time axis, and already reachable
      through ADR-0019's health probe, which benches an endpoint when its
      probe message carries one
- [ ] `status` command on ingest's `Controller`: rate, connection state, queue
      depth, worker count ([ADR-0015](adr/0015-tui-as-control-plane-client.md))
- [x] Re-measure throughput as distinct events per second. With duplicates at
      0.56% the two are now the same figure: the live tail settles at 230-250
      events/sec, matching the 250/s median
      ([ADR-0014](adr/0014-mart-grain-and-transform-cadence.md)), while
      backfill reads 32,000-38,000/s of real history rather than re-reads
- [x] A second instance prints `Error: ingest service is already running.`
      and exits 1, so the orchestrator can tell a refusal from a healthy
      start ([ADR-0007](adr/0007-control-plane-ipc.md),
      [ADR-0006](adr/0006-cli-orchestration-model.md))
- [ ] Retention: ingest prunes `events` below the position transform
      publishes ([ADR-0013](adr/0013-service-owned-pruning.md)). Lands with
      M2; until then rows accumulate at ~21 GB/day
- [ ] Prune `gap_index` on the retention floor — the durable coverage record
      once `events` is a buffer, growing ~400 MB/day unpruned

**Acceptance**

Demonstrated:

- [x] 99.93% second-level coverage over a continuous 24.3-hour window with
      zero gaps over 10s
- [x] `quick_check` returns `ok` on a 23.6 GB store while ingest keeps writing
- [x] 0.56% duplicates over a 24.24-hour window, which is the shard overlap
      and nothing more ([ADR-0010](adr/0010-deduplication-in-the-mart.md))
- [x] A degraded endpoint is screened out mid-run and the pool carries on
      ([ADR-0019](adr/0019-endpoint-witness-lag-screening.md))
- [x] A dropped connection re-queues the unread remainder rather than the
      whole range
- [x] `kill -9` mid-stream: `quick_check` returns `ok` on the 21 GB store, and
      the last durable event sits 7.9 s behind the kill — inside the ~41 s
      that 10,000 queued messages represent at the live rate
      ([ADR-0009](adr/0009-bounded-write-queues.md)). The next start's audit
      re-backfills that window, so the loss is recovered rather than merely
      bounded
- [x] Stop for a known duration, restart, and the audit reports a gap of that
      size at that position (TDD-0003 §1). Confirmed twice: a 14-minute clean
      stop and a 5.9-minute crash gap, each detected to the second and
      widened by the standard ten seconds at each edge
- [x] A second `sup ingest` against the same data directory refuses and exits
      non-zero ([ADR-0007](adr/0007-control-plane-ipc.md))
- [x] `kill -9` leaves a stale lock file and socket; the next start acquires
      the `flock` anyway and clears them (ADR-0007)
- [x] SIGTERM drains readers before the writer, then releases the listener and
      removes the socket ([TDD-0002](tdd/0002-cli-orchestration.md) §6)

- [x] The DLQ takes a row when one fails to parse, seen on an earlier run. It
      stays empty on a healthy one, so its rate is an instrument rather than
      a defect count
      ([ADR-0011](adr/0011-record-validation-and-routing-in-the-mart.md))

Untested:

- [ ] An unreachable endpoint backs off rather than reconnecting in a loop.
      Every run so far has had all six endpoints up

## M2: Minimal mart transform

- [x] `INSTALL ducklake` / `INSTALL sqlite` in `bootstrap()`
      ([ADR-0002](adr/0002-raw-ingestion-durability.md))
- [ ] DuckLake mart with a SQLite catalog, in its own file
- [ ] Transform reads the raw store through `aiosqlite` and writes to
      DuckLake, with the Pydantic validation and routing stage between
      ([ADR-0002](adr/0002-raw-ingestion-durability.md))
- [ ] Deduplicate on `(did, rkey, rev)`
      ([ADR-0010](adr/0010-deduplication-in-the-mart.md))
- [ ] Validate with Pydantic
      ([ADR-0011](adr/0011-record-validation-and-routing-in-the-mart.md)),
      including negative tests — a clean pass over real data does not show the
      union discriminates
- [ ] Route to per-collection tables on `commit.collection`, since deletes
      (3.8%) carry no record. One row per event, every revision kept
      ([ADR-0014](adr/0014-mart-grain-and-transform-cadence.md))
- [ ] Mart-side reject table, keeping each store to a single writer
      ([ADR-0013](adr/0013-service-owned-pruning.md))
- [ ] Cycle trigger at ~15,000 unconsumed rows, taking everything available
      (ADR-0014)
- [ ] Watermark column + state table for incremental runs
- [ ] Publish the committed position where ingest can read it (ADR-0013)
- [ ] Purge mart rows past the retention floor
      ([ADR-0012](adr/0012-rolling-retention-window.md)): `DELETE`,
      `ducklake_expire_snapshots`, `ducklake_cleanup_old_files`, on a schedule
- [ ] Control-plane socket + `flock` lock, same design as ingest's
- [ ] Graceful shutdown finishing the current cycle, working standalone
- [ ] Same pause/resume/quiesce treatment as ingest (TDD-0002 §6); quiesce is
      "finish or don't start a cycle"
- [ ] `status` command reporting the committed watermark (ADR-0015)

**Acceptance:** delete the mart, rebuild from the raw store, get the same
result. Settle the read path first: DuckDB attaching the raw store fails
intermittently with `database disk image is malformed` while ingest writes,
at any watermark distance, where SQLite's own connection does not
(ADR-0002).

## M3: Minimal operational TUI

- [ ] Connection status, total ingest rate across all connections (TDD-0003
      §6), ingest/mart lag, error log — all polled over the control plane,
      with no database connection in the TUI
      ([ADR-0015](adr/0015-tui-as-control-plane-client.md))
- [ ] Per-service state model (running, stopping, stopped, unreachable),
      shared by the status display and liveness monitoring
- [ ] Pause/shutdown sent to each service's Listener (TDD-0002 §6)
- [ ] `ctrl+q` runs the ordered shutdown as a Textual worker with each
      service's state changing in view; `ctrl+c` keeps Textual's default

**Acceptance:** the TUI reflects live status without a restart. Pause and
shutdown from the TUI stop ingestion, and resuming picks up with no gap or
duplication.

## M4: Minimal analytics dashboard

- [ ] Streamlit app, a handful of focused views, queries wrapped in
      `@st.cache_data`. Streamlit requires `pyarrow<25,>=7.0` and brings it in
      transitively, so leave pyarrow undeclared
- [ ] Bundled Streamlit config with `gatherUsageStats = false`
- [ ] `sup dashboard` manages the server process lifecycle
- [ ] One real question answerable end-to-end (e.g. post volume over time)

**Acceptance:** `sup dashboard` gets from "just installed" to a real chart of
real data without manual wrangling.

## Orchestration: bare `sup` runs everything

Ties M1–M4 together. Design in [TDD-0002](tdd/0002-cli-orchestration.md).

- [ ] Process spawn into separate process groups
      (`start_new_session=True` / `CREATE_NEW_PROCESS_GROUP`), TUI in-process
- [ ] Startup ordering: ingest → its socket becomes connectable → transform →
      dashboard → TUI
- [ ] Liveness monitoring: surface an unexpected exit as an error state, no
      auto-restart in v1
- [ ] Graceful shutdown in order: ingest → transform → dashboard → TUI
- [ ] Teardown after `app.run()` returns, covering exit paths no binding sees
      ([ADR-0015](adr/0015-tui-as-control-plane-client.md))
- [ ] Subprocess stdout/stderr to per-service log files
- [ ] Subcommands remain functional standalone

**Acceptance:** a clean install running bare `sup` reaches M4's outcome
without starting each component. Both a clean Ctrl+C and a `kill -9` of the
parent leave no orphaned subprocesses (TDD-0002 §8).

## M5: Hardening

- [ ] Deeper reconnect/backfill correctness
- [ ] Compaction/retention tuning under sustained load
- [ ] Expand mart schema / dashboard views
- [ ] Docs polish, ADRs for anything decided along the way
- [ ] `sup clean`: full data reset, pausing both services if running, with
      explicit confirmation ([ADR-0008](adr/0008-sup-clean-full-reset.md))
- [ ] Per-service log files, moved earlier if standalone runs need debugging
      before orchestration exists
