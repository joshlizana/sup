# ADR-0002: Durable storage for raw Jetstream ingestion

## Status

Accepted

## Context

`sup` ingests the Bluesky Jetstream firehose over a websocket and must persist
what it receives durably before (or as) it's transformed into the data mart.
The raw store is the system's source of truth — if it's lossy or corruptible
on crash, everything downstream inherits that unreliability, and for a
portfolio piece that's the kind of bug a reviewer running it locally is most
likely to actually hit (kill the process mid-stream, restart, see corrupted or
missing data).

Relevant facts to design against:
- Jetstream is filterable server-side by collection/DID, which bounds volume,
  but even a filtered stream (e.g. all `app.bsky.feed.post` events network-wide)
  can be a sustained high-message-rate stream, not a trickle.
- Jetstream supports resuming from a cursor after a reconnect, but it only
  replays a limited recent backlog — it is not a substitute for `sup`'s own
  durable storage, only a mitigation for short gaps around reconnects.
  **Confirmed** (was originally flagged here as needing verification):
  connecting with a cursor older than the roll-back window doesn't error —
  the server sends a "cursor too old" info message, then replays from the
  oldest available message, then continues live. This is now load-bearing
  for more than just reconnects — see
  [TDD-0003](../tdd/0003-ingest-backfill-and-gap-recovery.md), which uses
  this specifically to detect the current retention floor per endpoint on
  every start.
- Per ADR-0001, stdlib/third-party libraries (`sqlite3`, `duckdb`, etc.) are
  fair game; hand-rolling is reserved for genuine gaps.

Evaluation criteria: crash/restart safety, backpressure handling under a
high-message-rate stream, disk growth and compaction story, and how directly
the format feeds the downstream mart transform.

## Options considered

### Option A: Append-only JSONL segment files

- Sequential appends to a rotating file, buffered in memory and fsynced on a
  documented cadence.
- **Crash safety:** bounded, explicit loss window; a truncated trailing line
  needs repair on read.
- **Backpressure:** the cheapest possible write path, easily decoupled from
  the socket by a queue.
- **Disk/compaction:** rotate, gzip, delete once compacted into the mart.
- **Feeds the mart:** line-by-line into DuckDB or Parquet on transform.
- Hand-rolled, which ADR-0001 reserves for the project's core.

### Option B: SQLite as a durable append buffer

- A row per message, batched into transactions, WAL for crash safety and
  concurrent reads during writes.
- **Crash safety:** strong — transactional durability is well tested.
- **Backpressure:** sustained rates need careful transaction batching, with
  more to tune than a flat file.
- **Disk/compaction:** a single growing file; reclaiming space needs
  periodic `VACUUM`.
- **Feeds the mart:** invites querying the raw store directly, blurring the
  raw/mart boundary and risking lock contention with the TUI and dashboard.

### Option C: Streaming Parquet segment writer

- Buffer messages in memory, flush as Parquet row groups periodically.
- **Crash safety:** the buffer since the last flush is unprotected, so this
  needs Option A or B underneath to be durable at all.
- **Feeds the mart:** best-in-class, since DuckDB reads Parquet natively.
- A good format for the *mart*, a weak fit for the *raw durable store*.

### Option D: Hybrid — durable append log + separate mart build

- Option A or B as the durability layer, with a separate transform step
  building the mart from completed segments.
- Matches "save it durably, *then* transform it" as two steps, letting the
  write path optimize for safety and the mart for query performance.

### Option E: SQLite (WAL, `synchronous=NORMAL`) raw store + DuckLake mart

- Raw store is a single SQLite database, journal mode `WAL`, `synchronous=NORMAL`,
  written by a batched writer (see Decision below). WAL mode allows multiple
  concurrent readers while the ingest client writes.
- **Crash safety:** `synchronous=NORMAL` in WAL mode does not fsync on every
  commit (only at checkpoint), so committed transactions are durable across
  an application/process crash (e.g. `kill -9`), but a small window of the
  most-recently-committed transactions can be lost on power loss or an OS
  crash, since they may still be sitting in the WAL file unsynced. This is a
  known, documented SQLite tradeoff, not a bug — see
  [SQLite forum: process vs OS-level durability](https://sqlite.org/forum/info/9d6f13e346231916).
  It satisfies the `kill -9` restart test in TODO.md's M1 acceptance
  criterion; it does not claim anything stronger than that.
- **Backpressure:** avoiding fsync-per-commit is what makes this viable under
  a real message rate — but transaction overhead (WAL frame writes, lock
  acquisition) still means single-row-per-transaction writes would bottleneck
  under load regardless of `synchronous` mode. Requires explicit batching.
- **Disk/compaction:** grows unbounded unless rows are pruned after they're
  compacted into the mart (SQLite doesn't rotate/segment on its own the way a
  flat-file log does).
- **Feeds the mart:** DuckDB can `ATTACH` the SQLite file directly (via its
  SQLite extension) and run SQL transforms straight into DuckLake. This ADR
  originally took that to mean the entire raw→mart step would be one SQL
  script; [ADR-0011](0011-record-validation-and-routing-in-the-mart.md)
  since put a Python validation and routing stage in the middle, so the
  transform is a DuckDB read, a Pydantic pass, and a DuckLake write. The
  `ATTACH` bridge itself is unaffected. DuckLake reached v1.0 in April
  2026 and is described by DuckDB Labs as production-ready with
  backward-compatibility guarantees — see
  [DuckLake v1.0 announcement](https://ducklake.select/2026/04/13/ducklake-10/).
  This substantially reduces the maturity risk this ADR originally flagged
  for lakehouse-style mart formats.
- **New consideration DuckLake introduces — the catalog backend:** DuckLake
  stores its own metadata in a separate catalog database (DuckDB, SQLite, or
  Postgres are supported). Per
  [DuckLake: Choosing a Catalog Database](https://ducklake.select/docs/stable/duckdb/usage/choosing_a_catalog_database):
  a DuckDB catalog is most feature-complete but single-client only; Postgres
  is the only catalog DuckLake calls "production-grade with full
  parallelism" but is an external service; SQLite supports multiple local
  processes via a retry-timeout mechanism, without Postgres's full
  parallelism.
  `sup` needs the mart-transform writer, the operational TUI, and the
  analytics dashboard to all touch the catalog — three concurrent local
  processes with no appetite for an external service — which rules out a
  single-client DuckDB catalog and rules out Postgres per ADR-0003's
  zero-friction install goal.
- **Unresolved, and knowingly so:** DuckDB's SQLite integration has a
  documented *same-process* concurrency bug — crashes and lock errors when
  one connection writes to a SQLite file while another reads it
  ([duckdb-sqlite#82](https://github.com/duckdb/duckdb-sqlite/issues/82)).
  `sup`'s actual pattern (a separate ingest process writing WAL, a separate
  DuckDB process reading via `ATTACH`) is not the exact scenario reported
  broken, but nothing found confirms it's safe either. See Consequences.

## Decision

**Option E.** SQLite (WAL, `synchronous=NORMAL`) as the raw durable store,
DuckLake as the mart, DuckDB's SQLite extension as the bridge between them,
with the following resolved sub-decisions:

- **Write batching:** hybrid — the ingest client commits a batch every N
  messages or every X ms, whichever comes first. Bounds both memory (count
  limit) and latency (time limit) regardless of traffic pattern.
- **Retention:** raw rows are pruned from SQLite only after the corresponding
  DuckLake write is verified committed.
  [ADR-0012](0012-rolling-retention-window.md) supersedes the rest of this
  sub-decision: both stores are rolling windows on the retention floor, so
  neither is a long-term archive.
- **Incremental mart builds:** a monotonic watermark column plus a small
  state table tracks the last-processed point, so the transform reads
  `WHERE > watermark` instead of reprocessing or duplicating on every run.
- **DuckLake catalog backend:** SQLite, in its own file separate from the raw
  ingest store — avoids mixing high-frequency ingest WAL churn with catalog
  metadata I/O in the same file, and keeps the whole system free of external
  service dependencies.
- **Cross-process SQLite/DuckDB concurrency:** not pre-validated with a
  throwaway spike. This is a core piece of the pipeline being built anyway,
  in data-flow order (M1 ingest → M2 mart transform), so it gets validated as
  those milestones land. If it doesn't hold up under real construction,
  that's a design revisit at that point — see Consequences.

This supersedes the ADR's original recommendation (Option D with hand-rolled
JSONL segments as the durability primitive). SQLite is a more defensible,
battle-tested durability primitive than hand-rolled segment rotation and
crash-recovery, and DuckLake — now production-ready — gives the mart a real
format instead of the original recommendation's vaguer "DuckDB tables and/or
Parquet."

## Consequences

- The raw store stays bounded in size, but only if pruning is implemented
  correctly and ordered strictly after a verified DuckLake commit — pruning
  before that verification is a real data-loss path and needs to be treated
  as such in the implementation, not an afterthought.
- The watermark state table is new state that must itself be correct and
  durable; if it's wrong, the mart silently drifts (missed or duplicated
  rows) rather than failing loudly.
- A SQLite catalog trades DuckLake's "full parallelism" for staying
  dependency-free per ADR-0003. Data inlining stays available: 20,000 rows
  written in 10-row transactions produced zero Parquet files and a
  `ducklake_inlined_data_1_1` table holding all of them in the catalog.
  Revisit only if something needs Postgres-level catalog concurrency.
- The raw→mart transform runs as a SQL read, a Pydantic validation and
  routing stage, then a DuckLake write
  ([ADR-0011](0011-record-validation-and-routing-in-the-mart.md)). The
  pipeline stays legible end-to-end through the record models rather than
  through a single query.
- **Measured against the alternatives**, 2M real rows at the writer's
  10,000-row batch size:

  | Backend | rows/s | Concurrent read | Survives `SIGKILL` |
  |---|---|---|---|
  | Arrow IPC | 870,556 | yes | yes |
  | Parquet | 557,298 | no | no — lost all 17.16M rows |
  | SQLite | 268,607 | yes | yes |
  | DuckLake | 248,346 | yes | yes |
  | DuckDB single-file | 101,790 | no — exclusive lock | yes |

  A Parquet file stays unreadable until its footer lands, and DuckDB's
  single-file format locks out the TUI and mart. DuckLake's cost tracks
  commit frequency: at the 10-row batches the live tail produces it reaches
  460 rows/s against SQLite's 110,940, accruing a snapshot per flush. The
  split holds as chosen — SQLite for small frequent commits, DuckLake for
  large periodic ones.
- The raw store's connection carries a 5-second `busy_timeout` from
  `sqlite3.connect`'s default. A contended write waits for it, the first
  defence for the risk below. An explicit `timeout` argument would replace
  it.
- Cross-process SQLite/DuckDB concurrency is an **accepted open risk**,
  deliberately left unspiked. Flagged here and in TODO.md's M2 acceptance
  criteria so it is recognised on arrival rather than routed around.
- The project's hand-rolled surface (ADR-0001) is now the Jetstream client
  alone, since durability and the mart lean on libraries. The client carries
  enough on its own — protocol handling, reconnect and cursor logic,
  backpressure into the batched writer — with the hand-rolled identity
  narrower than ADR-0001 sketched.
