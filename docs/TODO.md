# TODO / Roadmap

Organized as a thin vertical slice: get a minimal version of every layer
working end-to-end (M0–M4) before deepening any one layer (M5+). See
[tdd/0001-architecture-overview.md](tdd/0001-architecture-overview.md) for the
layer breakdown these milestones map to.

No firm deadline — sequence and depth are driven by technical soundness and
learning value, not a ship date.

## M0: Project scaffold ✅

- [x] `pyproject.toml` with project metadata, console-script entry point
- [x] `uv` lockfile committed, `uv run sup` works from a clean checkout
- [x] Minimal README install instructions (`uvx sup` / `uv tool install`)

**Acceptance:** a clean checkout can be installed and produce a `sup --help`
via the documented `uv` command, with no manual steps.

## M1: Minimal ingest + durable raw store

- [x] Shared data directory: `Config` (`src/sup/config.py`) exposes
      `data_path` and `control_path` as computed sub-paths of one
      `platformdirs` root, created on access. See
      [TDD-0002](tdd/0002-cli-orchestration.md) Prerequisite.
- [x] Jetstream client: `JetstreamClient`
      (`src/sup/services/ingest/jetstream.py`) connects with retry/backoff
      at a given URL and cursor, receives one message at a time, closes.
      Thin by design, so shard workers, the live tail, and the
      retention-floor probe all share it.
- [x] Collection filter applied at connect time as repeated
      `wantedCollections` query params.
- [x] Range-based worker: `Reader` (`src/sup/services/ingest/reader.py`)
      reads forward from a `(start, end)` `time_us` range, stopping at
      `end`. The live tail is the same worker with `end = sys.maxsize`
      (TDD-0003 §4, §5).
- [x] Per-worker throughput: `Reader.update_throughput()` keeps a 10s
      rolling window; the TUI sums it across `Ingester.readers`
      (TDD-0003 §6).
- [x] Per-instance log identity: `sup.util.register()` returns a
      `LoggerAdapter` tagging every line with `Class:token`.
      `sup/__init__.py` holds the process's only `basicConfig()` call
- [x] Gap detection on every start: `GapAuditor`
      (`src/sup/services/ingest/audit.py`) mirrors `(pk, time_us)` into a
      DuckDB `gap_index`, scans it with a windowed `LAG(time_us)` for gaps
      over 10s, and adds leading/trailing ranges against the retention
      floor. See [TDD-0003](tdd/0003-ingest-backfill-and-gap-recovery.md) §1.
- [x] Retention-floor probe across all endpoints concurrently, with the
      configured default as a policy cap (TDD-0003 §1)
- [x] Tolerate partial probe failure: `GapAuditor._probe()` returns `None`
      on failure or after 15s, dropping that endpoint from the floor
      comparison; all-failed falls back to the configured retention
      (TDD-0003 §1)
- [x] Backfill sharding: `GapAuditor._shard_gaps()` splits gaps into 30-min
      ranges with a 10s overlap either side, appending the live tail as
      `(now, sys.maxsize)` — claimed last by design (TDD-0003 §2, §5)
- [x] Range workers re-enqueue a narrowed range on interruption
      (TDD-0003 §4)
- [x] Priority work queue of `(start, end)` ranges, oldest-first; plain FIFO
      message queues into the single writer (TDD-0003 §3)
- [x] SQLite raw store: WAL mode, `synchronous=NORMAL` (`SQLiteClient`,
      `src/sup/db.py`; schema in `src/sup/boostrap.py`)
- [x] Batched writer: `Writer` (`src/sup/services/ingest/writer.py`) drains
      both queues and commits each batch via `executemany`, sleeping 10ms
      when empty, with a final flush after the run loop. The queues'
      `maxsize=10,000` caps batch size, giving "N messages or X ms,
      whichever first" from one bound
      ([ADR-0009](adr/0009-bounded-write-queues.md))
- [x] Append-only raw store, deduplicated by the mart
      ([ADR-0010](adr/0010-deduplication-in-the-mart.md)). `orjson` replaces
      the stdlib JSON parser, record validation moves to M2, and the reader
      extracts its columns straight from the parsed message
- [ ] Retention: ingest prunes `events` below the position transform
      publishes, on its own cadence
      ([ADR-0013](adr/0013-service-owned-pruning.md)). Lands once M2 publishes
      a position; until then rows accumulate at ~15.6 GB/day
- [ ] Prune `gap_index` on the retention floor. It is the durable coverage
      record once `events` is a buffer, and grows ~260 MB/day unpruned
      ([ADR-0013](adr/0013-service-owned-pruning.md))
- [ ] `status` command on ingest's Controller: rate (summed from
      `Reader.update_throughput()`), connection state, queue depth, worker
      count ([ADR-0015](adr/0015-tui-as-control-plane-client.md))
- [x] Control-plane socket + `flock`'d single-instance lock, generic across
      services: `SupListener`/`SupClient` (`multiprocessing.connection`,
      authkey-secured, over a Unix domain socket / named pipe) plus
      `Controller`, which owns the accept loop and command dispatch. Built
      ahead of ingest as reusable infra. See
      [ADR-0007](adr/0007-control-plane-ipc.md) /
      [TDD-0002](tdd/0002-cli-orchestration.md) §2.
- [x] `Controller` dispatch: pause/resume/shutdown arrive over the Listener
      and bridge into the event loop via
      `asyncio.run_coroutine_threadsafe`. A self-connect wakes the accept
      loop's blocked `accept()` on shutdown, since only a real incoming
      connection unblocks a call already running in a worker thread. Pause
      and shutdown share one injected `quiesce()` hook; resume gets its own
      ([TDD-0002](tdd/0002-cli-orchestration.md) §6).
- [x] Ingest's `quiesce()`/`resume()`/`shutdown()` (`Ingester`,
      `src/sup/services/ingest/ingest.py`), injected into `Controller`.
      `drain()` carries the ordering: readers stop and are awaited first,
      re-enqueueing their narrowed ranges, then the writer commits what they
      left. `resume()` creates the next worker generation directly, keeping
      pause/resume timing independent of any supervising poll interval
      ([TDD-0002](tdd/0002-cli-orchestration.md) §6).
- [x] Ingest orchestrator (`Ingester`): acquires the lock before any work,
      runs `GapAuditor` to completion, seeds the work queue from its shards,
      constructs one `Reader` per endpoint plus the `Writer`, and idles
      until shutdown
- [ ] Graceful-shutdown signal handler (SIGTERM/Ctrl+C) wired to
      `Controller._shutdown()` — must work standalone as well as when
      orchestrated
- [ ] `sup ingest` subcommand registered on the Typer app (`src/sup/cli.py`
      currently has only the root callback). Last piece before M1 runs from
      the entry point
- [ ] Decide whether a commit message missing a required column should
      reach the DLQ. It is currently skipped without a record, which leaves
      an unrecorded loss path

**Acceptance:** run for an extended period, kill `-9` the process mid-stream,
restart, and confirm no corruption and a bounded/documented loss window.

## M2: Minimal mart transform

- [ ] Deduplicate on `(did, rkey, rev)` during the transform. Required for
      mart correctness at a measured ~2% duplicate share. See
      [ADR-0010](adr/0010-deduplication-in-the-mart.md)
- [ ] Type/validate records during the transform with Pydantic, per
      [ADR-0011](adr/0011-record-validation-and-routing-in-the-mart.md).
      `models.py` holds the schema. Verified against the full raw store:
      5,859,179 of 5,859,179 rows validate, at 271,511 rows/s
- [ ] Route validated records to per-collection mart tables, keyed on
      `commit.collection`, since deletes (3.8% of rows) carry no record to
      match on. One row per validated event, every revision kept
      ([ADR-0014](adr/0014-mart-grain-and-transform-cadence.md))
- [ ] Mart-side reject table for validation failures, keeping each store to a
      single writer ([ADR-0013](adr/0013-service-owned-pruning.md))
- [ ] Cycle trigger: run when ~15,000 unconsumed raw rows exist and take
      everything available, so batches track arrival rate
      ([ADR-0014](adr/0014-mart-grain-and-transform-cadence.md))
- [ ] Publish the committed position where ingest can read it, so ingest
      prunes against it ([ADR-0013](adr/0013-service-owned-pruning.md))
- [ ] `status` command reporting the committed watermark
      ([ADR-0015](adr/0015-tui-as-control-plane-client.md))
- [ ] DuckLake mart with a SQLite catalog, in its own file separate from the
      raw store
- [ ] DuckDB `ATTACH`es the raw store SQLite file and writes to DuckLake,
      with the Pydantic validation/routing stage in between (ADR-0011
      supersedes ADR-0002's one-SQL-script goal)
- [ ] Watermark column + small state table for incremental transform runs
- [ ] Mart is rebuildable from the raw store from scratch
- [ ] Wire up the M1 pruning step now that verified-commit is possible
- [ ] Purge mart rows past the retention floor
      ([ADR-0012](adr/0012-rolling-retention-window.md)). Three steps:
      `DELETE`, `ducklake_expire_snapshots`, `ducklake_cleanup_old_files` —
      `DELETE` alone holds the row count flat while disk grows. Runs on a
      schedule: ~1,400 commits/day produce that many snapshots
      ([ADR-0014](adr/0014-mart-grain-and-transform-cadence.md))
- [ ] Control-plane socket + `flock`'d single-instance lock on startup, same
      design as ingest's (see [TDD-0002](tdd/0002-cli-orchestration.md) §2)
- [ ] Graceful-shutdown signal handler (finishes current transform cycle,
      doesn't abort mid-write) — must work standalone
- [ ] Same pause/resume/quiesce treatment as ingest
      ([TDD-0002](tdd/0002-cli-orchestration.md) §6). Transform's quiesce is
      "finish or don't start a cycle"; the shape — shared coroutine,
      self-connect delivery, pause keeping lock and socket alive — matches
      ingest's.

**Acceptance:** delete the mart, rebuild it from the raw store, get the same
result. **This milestone is also where the cross-process SQLite/DuckDB
concurrency assumption from [ADR-0002](adr/0002-raw-ingestion-durability.md)
gets tested for real** (ingest process writing WAL while this transform reads
via `ATTACH`) — if it doesn't hold up under load, that's a milestone blocker
to resolve deliberately.

## M3: Minimal operational TUI

- [ ] Connection status, **total ingest rate across all active connections**
      (backfill shards + live tail combined — see
      [TDD-0003](tdd/0003-ingest-backfill-and-gap-recovery.md) §6),
      ingest/mart lag, error/reconnect log — all polled over the control
      plane, with no database connection in the TUI
      ([ADR-0015](adr/0015-tui-as-control-plane-client.md))
- [ ] Per-service state model (running, stopping, stopped, unreachable),
      shared by the status display and liveness monitoring
- [ ] Pause/shutdown commands sent to ingest's control-plane Listener (per
      [TDD-0002](tdd/0002-cli-orchestration.md) §6), making the TUI an
      active control-plane participant
- [ ] `ctrl+q` runs the ordered shutdown as a Textual worker, with each
      service's state changing in view before the app exits. `ctrl+c` keeps
      Textual's default notification

**Acceptance:** while ingest is running, the TUI reflects live status without
needing a restart. Pause and shutdown, triggered from the TUI, actually stop
ingestion (websocket disconnected, batch flushed) and — for pause — resuming
picks back up with no gap or duplication.

## M4: Minimal analytics dashboard

- [ ] Streamlit app with a handful of focused views (not a multi-page app),
      queries wrapped in `@st.cache_data`
- [ ] Bundled Streamlit config with `gatherUsageStats = false`
- [ ] `sup dashboard` manages the Streamlit server process lifecycle (launch,
      port, browser open, clean shutdown)
- [ ] At least one real question about the ingested content answerable
      end-to-end through the dashboard (e.g. post volume over time)

**Acceptance:** running `sup dashboard` on its own gets from "just installed"
to "seeing a real chart of real data" without manual data wrangling. (Bare
`sup` orchestrating everything together is its own step below.)

## Orchestration: bare `sup` runs everything

Ties M1–M4 together — can't meaningfully start before all four exist
individually. Design is broken down in
[TDD-0002](tdd/0002-cli-orchestration.md); this is that design's work
items as a checklist:

- [ ] Process spawn: orchestrator launches `sup ingest`/`sup transform`/
      `sup dashboard` as subprocesses of itself, each in its own process
      group (`start_new_session=True` / `CREATE_NEW_PROCESS_GROUP`) so
      Ctrl+C reaches only the main process; TUI runs in-process
- [ ] Startup ordering: ingest → (wait for its control socket to become
      connectable) → transform → dashboard → attach TUI
- [ ] Liveness monitoring: detect an unexpected subprocess exit, surface it
      as an error state in the TUI — no auto-restart in v1 (deferred to M5)
- [ ] Graceful shutdown, in order: signal ingest (finish current batch) →
      signal transform (finish current cycle, don't abort mid-write) → stop
      dashboard → TUI exits. Requires `sup ingest` and `sup transform` to
      each install their own graceful-shutdown signal handler
- [ ] Teardown after `app.run()` returns, catching the exit paths no key
      binding sees ([ADR-0015](adr/0015-tui-as-control-plane-client.md))
- [ ] Subprocess output: redirect each subprocess's stdout/stderr to its own
      log file under the shared data directory
- [ ] Individual subcommands (`sup ingest`, `sup transform`, `sup tui`,
      `sup dashboard`) remain functional standalone, for development/debugging

**Acceptance:** a clean install, running bare `sup` with no arguments,
reaches the same "seeing a real chart of real data" outcome as M4 —
without manually starting each component. Both a clean Ctrl+C and a
`kill -9` of the parent process leave no orphaned subprocesses behind
(TDD-0002 §7 is the test procedure to actually run, including the
single-instance-lock verification).

## M5: Hardening (post thin-slice)

- [ ] Deeper reconnect/backfill correctness
- [ ] Compaction/retention tuning under sustained load
- [ ] Expand mart schema / dashboard views
- [ ] Docs polish, ADRs for any decisions made along the way that weren't
      pre-recorded
- [ ] `sup clean`: full data reset (dev/testing utility). Works whether
      ingest/transform are running (pause both via control plane, wipe,
      resume both) or not (locks free → wipe directly). Requires explicit
      confirmation (prompt or `--yes`/`--force`) — destructive, not a bare-
      invocation operation. See
      [ADR-0008](adr/0008-sup-clean-full-reset.md).
