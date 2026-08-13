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
  durable storage, only a mitigation for short gaps around reconnects. (The
  exact retention window should be verified against current Jetstream docs
  before relying on it for anything beyond "cover the reconnect gap.")
- Per ADR-0001, stdlib/third-party libraries (`sqlite3`, `duckdb`, etc.) are
  fair game; hand-rolling is reserved for genuine gaps.

Evaluation criteria: crash/restart safety, backpressure handling under a
high-message-rate stream, disk growth and compaction story, and how directly
the format feeds the downstream mart transform.

## Options considered

### Option A: Append-only JSONL segment files

- Sequential appends to a rotating file (by size or time window), buffered in
  memory and flushed/fsynced on a documented cadence (e.g. every N messages or
  every N ms).
- **Crash safety:** bounded, explicit loss window (whatever's unflushed at
  crash time). A truncated last line on crash is possible and must be handled
  on read (skip/repair trailing partial line).
- **Backpressure:** very cheap to write (pure sequential append), decouples
  socket-read from disk-write easily via a queue.
- **Disk/compaction:** trivial — rotate, gzip completed segments, delete once
  compacted into the mart.
- **Feeds the mart:** straightforward — read line-by-line, parse JSON, load
  into DuckDB (`read_json_auto` or similar) or Parquet on transform.
- This is genuinely hand-rolled (per ADR-0001, that's intentional here — it's
  close to the project's core).

### Option B: SQLite as a durable append buffer

- Each message inserted as a row (batched into transactions for throughput),
  WAL mode for crash safety and concurrent reads during writes.
- **Crash safety:** strong — SQLite's transactional durability is well-tested.
- **Backpressure:** insert-per-message is fine in small batches, but a
  sustained high rate needs careful transaction batching to avoid becoming the
  bottleneck; more moving parts to tune than a flat file.
- **Disk/compaction:** a single growing file; deleting old rows requires
  periodic `VACUUM` or it fragments/doesn't reclaim space.
- **Feeds the mart:** tempting to query directly rather than transform,
  which blurs the raw-store/mart boundary and risks lock contention between
  the ingester (writer) and TUI/dashboard (readers) on the same file.

### Option C: Streaming Parquet segment writer

- Buffer messages in memory, flush as Parquet row groups/files periodically.
- **Crash safety:** weakest of the three — Parquet's columnar format isn't
  meant for single-row streaming appends, so the in-memory buffer since the
  last flush is unprotected. Would need a separate WAL just to cover that
  window, at which point you're really running Option A or B underneath it.
- **Feeds the mart:** best-in-class — DuckDB reads Parquet natively and fast.
- Good format for the *mart*, weak fit for the *raw durable store*.

### Option D: Hybrid — durable append log + separate mart build

- Use Option A (or B) purely as the durability layer, decoupled from the
  mart. A separate transform step (batch or periodic) reads completed
  segments and builds the mart (DuckDB tables and/or Parquet files).
- Matches the architecture the project description already implies: "save it
  durably, *then* transform it into a data mart" is two steps, not one.
- Cleanest separation of concerns: the write path optimizes for
  safety/throughput, the mart optimizes for query performance, and neither
  design compromises for the other.

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
  SQLite extension) and run SQL transforms straight into DuckLake — the
  entire raw→mart step can be one SQL script. DuckLake reached v1.0 in April
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
  processes via a retry-timeout mechanism, at the cost of some DuckLake
  features (e.g. data inlining) and without Postgres's full parallelism.
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
  DuckLake write is verified committed. SQLite is not kept as a permanent
  archive; DuckLake is the long-term store.
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
- Using SQLite as the DuckLake catalog means giving up some DuckLake
  features (e.g. data inlining) and "full parallelism" in exchange for
  staying dependency-free per ADR-0003. Worth revisiting only if a future
  need genuinely requires Postgres-level catalog concurrency.
- The entire raw→mart transform being expressible as one SQL script (SQLite
  attach → transform → DuckLake write) is a real portfolio strength worth
  highlighting in the eventual README/demo — it's an unusually legible
  pipeline for a stranger to read end-to-end.
- Cross-process SQLite/DuckDB concurrency safety is an **accepted open
  risk**, not a resolved question — it wasn't spiked ahead of time by
  design. This is flagged explicitly here and in TODO.md's M2 acceptance
  criteria so that if it surfaces as a problem, it's recognized as the known
  risk it is rather than a surprising new bug, and doesn't get silently
  routed around without reconsidering the architecture.
- The hand-rolled surface of the project (per ADR-0001) now rests entirely on
  the Jetstream client itself, since durability and the mart both lean on
  libraries. That's a fine tradeoff given the client is nontrivial on its
  own (protocol handling, reconnect/cursor logic, backpressure into the
  batched writer), but it's worth being aware the "hand-rolled" identity is
  now narrower than ADR-0001 originally sketched.
