# Changelog

All notable changes to this project are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `README.md`, `LICENSE` (MIT), and `CLAUDE.md`.
- Typer CLI scaffold (`src/sup/cli.py`), completing M0.
- `src/sup/config.py`: frozen pydantic `Config` with `platformdirs` paths.
- `src/sup/control/`: `SupListener`/`SupClient` over
  `multiprocessing.connection` with a `flock` single-instance lock and
  persisted authkey, plus `Controller` — accept loop and
  pause/resume/shutdown dispatch. Built ahead of ingest as shared infra.
- `src/sup/services/ingest/`: `JetstreamClient`, `Reader`, `Writer`,
  `GapAuditor` and `Ingester`. Plus `src/sup/db.py`, `src/sup/boostrap.py`,
  and `src/sup/util.py`.
- `src/sup/services/transform/models.py`: the Pydantic message shape the
  mart validates through, covering every record-derived column in
  [TDD-0004](tdd/0004-mart-schema.md) — `reply` and `embed` as typed
  subtrees, `facets` as a list of features, and one `StrongRef` for
  `subject`, `via`, `root` and `parent`. `commit.collection` and
  `commit.operation` are `Literal`s; embed and feature `$type` are open
  strings, so an unrecognised NSID validates and reaches the mart as
  written. 600,000 sampled messages validate with no rejects, and twenty
  crafted failures reject on the expected error
  ([ADR-0011](adr/0011-record-validation-and-routing-in-the-mart.md)).
- `bootstrap_ingest()` creates the raw store and gap index schemas;
  `bootstrap_transform()` creates the six mart tables and the single-row
  `watermark` table. `sup ingest` and `sup transform` each call their own.
  The `ducklake` and `sqlite` DuckDB extensions install in
  `DucklakeClient.__aenter__` ahead of the `LOAD`. Both are repository
  extensions fetched into `~/.duckdb` on first use (~71 MB), so a blocked
  download fails at the connection rather than inside a transform cycle. A
  repeat install costs ~1 ms and reaches no network.
- Graceful-shutdown signal handler on `Controller`, multiplatform via
  `signal.signal` plus `call_soon_threadsafe`. `_stopping` latches so a
  second signal or a draining pause cannot run the hook twice.
- Ingest lifecycle logging across `Ingester`, `GapAuditor` and `Reader`,
  plus `sup.util.displayTime`.
- TDD-0002: orchestration work breakdown, and the shared data-directory
  prerequisite it exposed for M1.
- TDD-0003: every-start gap detection against a cross-endpoint retention
  floor, parallel sharded backfill, and one priority work queue.
- TDD-0004: mart columns for all five collections plus the reject table,
  derived from a full pass over a 28,996,394-event raw store. Records the
  `via` strong ref on 3,402,182 likes, 863,199 reposts and 322,855 follows,
  the six embed types, and 40 undeclared extension keys that stay out of
  typed columns.
- ADR-0006: bare `sup` supervises ingest, transform and dashboard as
  separate processes; the TUI stays in the main process as the control
  surface. Headless rejected against the single-operator charter.
- ADR-0007: control-plane IPC over `multiprocessing.connection`, not TCP,
  with `chmod(0o600)` and a persisted authkey.
- ADR-0008: `sup clean`, a destructive full data reset requiring explicit
  confirmation.
- ADR-0009: write queues bounded at 10,000, chosen by measuring durable
  write rate across the range.
- ADR-0010: the raw store is append-only and the mart deduplicates.
  Dropping `UNIQUE(did, rkey, rev)` took throughput from 17.5k rows/s
  degrading to ~29–35k flat, CPU from 109% of a core to 66%, and peak RSS
  from 1,137 MB to ~470 MB.
- ADR-0011: the M2 transform validates and routes with Pydantic, at 271,511
  rows/s. Records two discriminator constraints found by testing.
- ADR-0012: both stores are rolling 24-hour windows. Purging DuckLake takes
  three steps — `DELETE` alone frees no disk.
- ADR-0013: each service prunes its own store. The transform publishes its
  committed position; ingest prunes against it, keeping one writer per
  store. `gap_index` becomes the durable coverage record.
- ADR-0014: a mart row is one validated event, every revision kept, with
  cycles triggering on ~15,000 unconsumed rows rather than a timer.
- ADR-0015: the TUI is a control-plane client holding no database
  connection. Both services answer a `status` command; `ctrl+q` runs the
  ordered shutdown with each service's state visible.
- ADR-0016: Jetstream connections pass `ping_interval=None`. Readers were
  closing their own healthy connections because a pong queues behind
  saturated data — an explicit ping on one connection at 4,180 msg/s failed
  to round-trip within 10 s against a 20 s timeout.
- ADR-0018: event time comes from `commit.rev`, decoded and stored as
  `tid_us`. `time_us` is a witness stamp; `createdAt` is client-supplied,
  absent on the 3.78% that are deletes and spanning 24 years past to 56
  years future.
- ADR-0019: readers screen their endpoint's witness lag during the retention
  probe, claiming no work when it fails. `jetstream2.us-east` stamped 100% of
  4,291,546 rows over an hour late across two runs, against 0.19–0.32 s
  medians elsewhere.
- ADR-0020: the two v2 endpoints clamp any cursor newer than a fixed replay
  floor, measured at 87 and 116 minutes. Backfill runs below the floor and
  is clean; the live tail reconnects seconds behind and replays the window,
  compounded by `current_cursor` moving backward. This is the cause of the
  46.19% duplicate share.
- ADR-0021: ingest and the transform each run a `Maintenance` instance
  against the stores they own, taking the connection that service already
  writes through and running after its write pass. Each step commits its own
  transaction, so an interrupted pass resumes.
- ADR-0023: the transform's committed position is a single row in
  `watermark.db`, read by ingest through `aiosqlite`. SQLite suits a counter
  overwritten once per cycle, and the read stays off DuckDB's SQLite
  extension, which ADR-0002 measured failing intermittently against a file
  another process is writing. Measured on DuckLake for comparison: 11.7 ms
  per write at 500 accumulated snapshots and 13.9 ms at 5,000, 0.13 KB of
  catalog per snapshot, no Parquet written.
- ADR-0027: the transform runs one sequential cycle over 100,000-row
  chunks, each validated and flattened in a single pass, bucketed into five
  Arrow tables, and flushed in one transaction before the next read. One
  chunk costs ~717 ms end to end (125 ms read, 456 ms validate and flatten,
  123.5 ms insert, 12 ms commit) at 139,000 rows/s, and peaks at 209.9 MB
  whatever the backlog holds. Fanning across five tables costs 1.24 µs per
  row at 100,000 against 6.09 µs at 15,000.
- ADR-0026: every mart table carries `k1` and `k2` `UBIGINT`, a hash of
  `did||rkey||rev` and a salted hash of it, computed in the
  `INSERT ... SELECT` over the registered Arrow table. The insert keeps one
  row per key pair within the chunk and anti-joins the pair against the
  destination, with the five collections sharing one transaction. Measured
  on 20,000,000 real keys per 15,000-row chunk: 53.2 ms for the pair, 43.0 ms
  for a single 64-bit hash, 166.1 ms for a concatenated string at 32.2 extra
  bytes per row, 224.1 ms for a three-column join.
- ADR-0025: registered Arrow tables and `USE` belong to the connection that
  declared them, so the statements reading them run through
  `execute_on_self`. Committed rows cross handles freely. `register` then
  `execute` raises `CatalogException` on the first chunk in 0.19 s;
  `execute_on_self` mirrors 2,000,000 rows in 20 chunks in 2.4 s, at 837,935
  rows/s.
- ADR-0024: the models close the types routing reads — `commit.collection`,
  `commit.operation`, the record union — and leave embed and facet feature
  `$type` as open strings. Closing those two would reject 2 posts of 24,602
  carrying an embed and 4 features of 31,893, each over a subtree no column
  requires.
- ADR-0022: `boostrap.py` holds one bootstrap function per service, each
  called from that service's invocation. A single `bootstrap()` ahead of
  Typer's dispatch runs for `sup --help` and in all four orchestrated
  subprocesses, and adding the mart tables to it would make ingest, the
  dashboard and the TUI writers of the mart.

### Changed

- The `posts` table declares eleven record-derived columns. Record-level
  `tags` (0.94%), `self_labels` (1.33%) and the client-name `via` (0.57%)
  are read from the raw store within its retention window.
- ADR-0002 accepted after measuring SQLite, DuckLake, DuckDB single-file,
  Arrow IPC and Parquet on 2M real rows. DuckDB's single-file format locks
  out concurrent readers; raw Parquet lost all 17.16M rows on `SIGKILL`;
  DuckLake falls to 460 rows/s at live-tail batch sizes.
- Cross-process concurrency narrowed to the reader: SQLite's own connection
  reads a bounded batch 5/5 while ingest writes, DuckDB's `sqlite_scanner`
  1/5 at a 100k lag and 4/5 at 1M, and `quick_check` returns `ok`
  throughout. The mart's read path goes through DuckDB, so the risk is open.
- A clean 24-hour run reached 99.93% second-level coverage with zero gaps
  over 10 seconds, meeting M1's acceptance criterion on live data.
- Live volume: ~27M events and ~21 GB of raw store per 24 hours, at 780
  bytes per row. Earlier figures counted a store inflated by the duplicate
  defect and were high on rows, low on distinct events.
- TDD-0003 §6 and TODO.md had the TUI summing `Reader.update_throughput()`
  across `Ingester.readers`, which ADR-0006 made unreachable by putting
  ingest in a subprocess. `Ingester` now sums them into its `status` reply.
- TDD-0002 §6 resolved: acknowledgment blocks until the hook returns, ingest
  holds no separate cursor state, and resume creates the next worker
  generation itself.
- Code comments and docstrings across `src/` reduced to what the code does;
  rationale and measurements live in the ADRs.
- Verified against live Jetstream: event messages carry exactly `did`,
  `time_us`, `kind` and `commit`, and `wantedCollections` for follows and
  blocks are `app.bsky.graph.*`.

### Fixed

- The 46.19% duplicate share, caused by the live tail replaying against
  endpoints that clamp recent cursors and by `current_cursor` regressing to
  the clamped position (ADR-0020). A run afterwards measured 0.56% over
  27,048,844 rows — flat across every pk region, 0.00% in the region the
  live tail wrote, and matching the 0.556% shard overlap alone predicts.
  99.44% of identities appear once and two rows in 27 million appear more
  than twice.
- `bulk_insert` rolls back before re-raising, making a batch atomic on the
  failure path. A partial `executemany` previously left its earlier rows in
  an open transaction for the next flush to commit.
- Commands arriving during the gap audit: worker objects are constructed at
  acquisition, task creation waits for the service to be active, and
  `drain()` runs safely before the tasks exist.
- `models.py`'s discriminated union selected on a field named `type` with
  `alias="$type"`, so a record's own bare `type` key could supply the tag.
  Renaming it `record_type` leaves `$type` as the only key that can.
- ADR-0002 listed data inlining as lost to the SQLite catalog. Inlining is
  active: 20,000 rows in 10-row transactions produced zero Parquet files.
- ADR-0002 and TDD-0001 described the raw→mart step as one SQL script;
  ADR-0011 puts a Python validation stage in that path.
- TDD-0003 §3 conflated the priority work queue of ranges with the FIFO
  queues feeding the writer.
- TDD-0003 stated the live tail should not be starved by backfill. The
  opposite is intended — backfill ranges are the ones racing the retention
  floor.
- TDD-0003 promised a durable record of permanently-lost ranges. Recorded as
  a non-goal: their absence is self-evident from the store.
- TDD-0002 §6 gave ingest's quiesce no ordering. The sequence is readers →
  drain → writer; stopping producer and consumer together strands queued
  messages.
