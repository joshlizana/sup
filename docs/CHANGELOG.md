# Changelog

All notable changes to this project are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Top-level `README.md` with project status, quick start, and docs links.
- `LICENSE` (MIT) and corresponding `license` field in `pyproject.toml`.
- `CLAUDE.md` documenting the docs-only working agreement and architecture
  summary for future Claude Code sessions.
- Initial Typer CLI scaffold (`src/sup/cli.py`), M0 complete.
- ADR-0006: bare `sup` orchestrates ingest/transform/TUI/dashboard as
  supervised separate processes.
- TDD-0002: orchestration work breakdown (process spawn, startup ordering,
  liveness, graceful shutdown sequence, subprocess logging) and the shared
  data-directory prerequisite it exposed for M1.
- ADR-0007: control-plane IPC via `multiprocessing.connection` (Unix domain
  socket / named pipe, not TCP), with a `flock`'d single-instance lock
  design for `sup ingest`/`sup transform`, folded into TDD-0002 and M1/M2.
- ADR-0007 correction: Unix socket files are not restrictive by default
  (`0755` typical) — added explicit `chmod(0o600)` + persisted, atomically-
  created `authkey` file as required, complementary hardening.
- TDD-0002 §6: pause/shutdown design — shared `_quiesce()` coroutine
  (disconnect websocket, flush write queue), pause keeps the process/lock/
  control socket alive for resume (which reuses reconnect/cursor-resume),
  commands delivered by connecting to the Listener rather than an external
  flag. TUI is no longer read-only (M3, TDD-0001 [4a] updated to match).
- `src/sup/config.py`: pydantic `Config` (frozen, `platformdirs`-based paths
  via `computed_field`).
- `src/sup/control/`: `SupListener`/`SupClient` (`control/connection.py`) —
  `multiprocessing.connection` Listener/Client wrapped with a `flock`-based
  single-instance lock and persisted authkey — plus `Controller`
  (`control/control.py`), the generic async accept loop and
  pause/resume/shutdown command dispatch built on top of them. Built ahead
  of ingest itself as reusable control-plane infra.
- Resolved: transform gets full pause/shutdown/quiesce symmetric to ingest
  (TDD-0002 §6 updated).
- ADR-0008: `sup clean` — full data reset. Destructive dev/testing utility;
  pauses both services first if running (via existing pause/resume), wipes
  directly if not; requires explicit confirmation.
- TDD-0003: ingest backfill and gap recovery. Every-start gap detection
  against a cross-endpoint retention floor, parallel sharded backfill
  (30 min shards, re-enqueue-narrower on interruption), single priority
  queue keyed on `time_us` (oldest first, no separate backfill/live tier
  needed), TUI shows total ingest rate across all connections. Confirmed
  the PK-based watermark is safe against out-of-order (backfill) writes.
  TDD-0001 [1]/[4a] and TODO.md M1/M3 updated to match.
- TDD-0003 retention-floor mechanism confirmed against Jetstream's actual
  docs: `cursor=1` triggers a "cursor too old" info message + full
  roll-back-window replay, not a lightweight probe — retention-floor
  detection must read the first message then disconnect immediately.
  ADR-0002's original "needs verification" note resolved to match.
- `src/sup/services/ingest/`: `JetstreamClient` (connect/recv/close plus
  retention probe), `Reader` (range worker), `Writer` (batched raw-store
  writer), `GapAuditor` (every-start gap detection), and Pydantic models for
  Jetstream commit messages. Plus `src/sup/db.py` (`SQLiteClient`/
  `DuckDBClient`), `src/sup/boostrap.py` (schema creation), and
  `src/sup/util.py` (`register()`/`Identity`, a `LoggerAdapter` that tags
  every log line with a per-instance `Class:uuid` identifier).

- ADR-0009: in-memory write queues bounded at `maxsize=1000` each, making
  M1's "bounded loss window" a computable ~33 ms, giving readers
  backpressure, and capping the writer's `executemany` batch by
  construction. TDD-0003 and TODO.md M1 updated to match.

- ADR-0010: the raw store is append-only and the mart deduplicates.
  Dropping `UNIQUE(did, rkey, rev)` and its 6x random-B-tree write penalty
  took durable throughput from 17.5k rows/s (degrading) to ~29–35k rows/s
  (flat to 1.9M rows), CPU from 109% of a core to 66%, and peak RSS from
  1,137 MB to ~470 MB. `orjson` replaces the stdlib JSON parser and
  ingest-time Pydantic validation moves to M2. Jetstream endpoint capacity
  (~37k msg/s) is now the binding constraint.

- ADR-0011: the M2 transform validates and routes records with Pydantic,
  using `models.py`'s discriminated union for per-collection columns and
  `commit.collection` for table routing. Measured at 271,511 rows/s against
  an in-engine SQL equivalent's 524,040, a round trip costing 21.6 s per
  24-hour window. Records two discriminator constraints found by testing:
  the field cannot be named `type`, and `discriminator="$type"` raises
  `PydanticUserError` at import.

- ADR-0006 amended: bare `sup` is the only run mode, with the TUI staying in
  the main process as the control surface. Headless was considered and
  rejected against the single-operator charter. Subcommands are how the
  orchestrator spawns each service and stay usable by hand, without being a
  supported product surface. Records the terminal cost of a TUI subprocess,
  and that Ctrl+C reaches every child in the process group — measured — so
  the three services spawn into their own groups. TDD-0002 §2/§5 and
  TODO.md updated to match.

- ADR-0012: both stores default to rolling 24-hour windows, superseding
  ADR-0002's "DuckLake is the long-term store". `Config.retention` names the
  window: backfill clamps it to what Jetstream still serves, the purge reads
  it directly, so raising it accumulates a longer archive. Purging DuckLake
  takes three steps — `DELETE`
  writes markers and adds a snapshot without freeing disk; only
  `ducklake_expire_snapshots` then `ducklake_cleanup_old_files` reclaim
  space. TDD-0001 [2] and TODO.md M2 updated to match.

- Ingest lifecycle logging: `Ingester` reports acquisition, worker
  generations, drains, and a 10-second status line; `GapAuditor` its
  retention floor and per-step counts; `Reader` the ranges it claims and
  re-queues. `sup.util.displayTime` renders `time_us` as UTC, showing the
  live tail's `sys.maxsize` end as "Forever".

- `Ingester` (`src/sup/services/ingest/ingest.py`): the ingest orchestrator,
  wiring the audit, readers, writer, and control plane into one lifecycle
  with pause/resume/shutdown. Verified against live Jetstream through
  startup, pause, resume, double-resume, and shutdown-during-startup.

- ADR-0013: each service prunes its own store. The transform publishes its
  committed position; ingest reads it and prunes `events` on its own cadence,
  keeping one writer per store. `gap_index` becomes the durable coverage
  record — it already fills incrementally and survives front-pruning — and
  gains a retention-floor prune of its own at ~260 MB/day. Raw steady state
  drops from ~15.6 GB to about one cycle's backlog.
- ADR-0014: a mart row is one validated event, every revision kept, with
  cycles triggering on ~15,000 unconsumed rows rather than a timer. Batches
  then track arrival rate — large during backfill, ~60 s at the live tail —
  which keeps DuckLake off the 460 rows/s floor its commit frequency imposes.
  Resolves TDD-0001's mart-schema and every-version-vs-latest questions.
- ADR-0015: the TUI is a control-plane client holding no database connection.
  Ingest and transform each answer a `status` command; `ctrl+q` runs the
  ordered shutdown with each service's state visible as it goes. Restores
  TDD-0001's mart-only rule and narrows ADR-0002's cross-process concurrency
  risk to transform-writes/dashboard-reads.

### Changed

- Live volume re-measured against actual coverage rather than the span
  between first and last row: the sampled window is 39.5% covered, so earlier
  figures extrapolated from it were low by ~2.3x. Across hours with
  near-complete coverage the five collections produce 235–275 messages/sec
  (median 250) at 721 bytes/row — ~21.6M rows and ~15.6 GB of raw store per
  day. Diurnal variation is unmeasured. TDD-0003 §6 updated.
- TDD-0003 §6 and TODO.md M1 had the TUI summing `Reader.update_throughput()`
  across `Ingester.readers`, which ADR-0006 made unreachable by putting ingest
  in a subprocess. `Ingester` now sums them into its `status` reply.
- ADR-0002's raw-store choice measured against SQLite, DuckLake, DuckDB's
  single-file format, Arrow IPC and Parquet on 2M real rows. DuckDB's
  single-file format holds an exclusive cross-process lock, so nothing reads
  while ingest writes; raw Parquet lost all 17.16M committed rows on
  `SIGKILL`, having no footer; DuckLake's cost scales with commit frequency,
  reaching 460 rows/s at live-tail batch sizes against SQLite's 110,940.
  Confirms Option E's split. Full table in the ADR.
- TDD-0002 §6 resolved two open questions: command acknowledgment blocks
  until the hook returns, and ingest holds no separate cursor state (the
  every-start audit derives position from `events`). Records that resume
  creates the next generation of workers itself rather than leaving it to a
  supervising loop.
- TDD-0003 §3/§4 updated for the bounded message queues, the per-endpoint
  retention rejection path, and column extraction from the parsed message.
- Code comments and docstrings across `src/` reduced to what the code does;
  rationale, measurements, and tradeoffs live in the ADRs.
- ADR-0009 revised: write queues bounded at 10,000 rather than 1,000, chosen
  by measuring durable write rate across the range. 1,000 → 10,000 gains
  ~13%; 1,000,000 *loses* throughput, because unthrottled readers starve the
  writer's thread while 1.1 GB accumulates in memory. Also corrected the
  writer's measured ceiling: the original ~372k rows/s figure was taken on a
  tmpfs-backed database with sequential synthetic keys and overstated
  disk-backed performance on real data by roughly an order of magnitude.
- TDD-0003 §6: the live stream produces ~230 messages/sec across the five
  configured collections, so backfill outruns real time by more than two
  orders of magnitude and a full 24-hour window (~20M events) drains in
  around ten minutes.
- Verified against live Jetstream: event messages carry exactly `did`,
  `time_us`, `kind`, and `commit` — there is no top-level `cursor` field, and
  `wantedCollections` for follows/blocks are `app.bsky.graph.*`, not
  `app.bsky.feed.*`. `cursor=0` behaves as the retention probe requires,
  yielding a first commit roughly 24h old.

### Fixed

- `bulk_insert` now rolls back before re-raising, making a batch atomic on
  the failure path as well as the success path. A batch failing partway
  through `executemany` previously left its earlier rows in an open
  transaction for the next flush to commit, once per retry attempt.
- Commands arriving during the gap audit are now handled: worker objects are
  constructed at acquisition, task creation waits for the service to be
  active and unpaused, and `drain()` runs safely before the tasks exist.
  Verified on both paths — shutdown leaves no orphaned tasks, and pause
  keeps the run loop alive so resume starts exactly one generation.
- `models.py`'s discriminated union selected on a field named `type` with
  `alias="$type"`, so a record carrying its own bare `type` key could supply
  the tag instead of `$type`. Naming the field `record_type` leaves `$type`
  as the only key that can. All 5,859,179 rows in the raw store now
  validate, including the one that previously failed.
- ADR-0002 listed data inlining as a DuckLake feature lost to the SQLite
  catalog. Inlining is active: 20,000 rows written in 10-row transactions
  produced zero Parquet files and a `ducklake_inlined_data_1_1` table
  holding all of them in the catalog.
- ADR-0002 and TDD-0001 [3] described the raw→mart step as one SQL script.
  ADR-0011 puts a Python validation stage in that path; both amended, along
  with TODO.md's M2 item.

- TDD-0003 §3 described a single priority queue of *messages* keyed on
  `time_us`. That conflated the priority **work** queue of `(start, end)`
  ranges with the queues feeding the writer, which are plain FIFO by design —
  §7's PK-based watermark already makes message insertion order irrelevant,
  so ordering them would cost a heap comparison per message for nothing.
- TDD-0003 stated the live tail should not be starved by backfill. The
  opposite is intended: the tail sorts last under the work queue's ordering
  key deliberately, because backfill ranges are the ones racing the retention
  floor. §5 rewritten to state this, with the reason deferring it costs no
  completeness.
- TDD-0003 promised an explicit durable record of permanently-lost ranges.
  Recorded as a non-goal instead: data past the retention floor is
  unrecoverable by definition and its absence is self-evident from the store,
  so a separate record would be redundant state to keep correct.
- TDD-0002 §6 described ingest's quiesce as "disconnect + flush" without an
  order. Stopping readers and writer together strands queued messages; the
  sequence is now spelled out (readers → drain → writer).
