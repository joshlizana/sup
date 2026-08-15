# ADR-0013: Each service prunes its own store

## Status

Accepted. Supersedes ADR-0002's and ADR-0012's "prune raw rows after a
verified mart commit".

## Context

ADR-0012 made both stores rolling windows. It left the raw store's prune
trigger as "after a verified DuckLake commit", written as though the transform
would delete the rows it had consumed.

Three facts bear on it. Measured across the five configured collections, the
raw store holds 7,125,333 rows in 5.14 GB, at 721 bytes per row. Taking the
rate from the five sampled hours with at least 50 minutes of coverage —
234.7 to 274.2 rows/s, median 250.4 — a full day is ~21.6M rows and ~15.6 GB,
the product's largest disk consumer. Diurnal variation is unmeasured, so treat
the range 14.6–17.1 GB as the estimate rather than the median alone.

A transform that deletes raw rows opens a write connection to the SQLite file
ingest is writing, turning a read-only attachment into a second writer on the
same lock — the cross-process pattern ADR-0002 accepted as unvalidated.

TDD-0003 §1 derives ingest's position by scanning `events` on every start, and
[TDD-0002](../tdd/0002-cli-orchestration.md) §Open resolved that ingest holds
no separate cursor state. Pruning `events` to a buffer therefore leaves the
audit reading a nearly-empty table and re-backfilling the window on every
restart.

## Options considered

### Option A: Raw keeps a full 24-hour window

- Prune raw on the retention floor rather than on mart commit.
- Pros: one retention rule for both layers; the gap audit is unchanged.
- Cons: ~15.6 GB of raw store as steady state, holding a second copy of what
  the mart already has in compressed Parquet.

### Option B: Transform prunes what it consumed

- The transform deletes raw rows after committing them.
- Pros: raw stays small, and the prune is trivially correct against the commit
  it follows.
- Cons: the transform writes to the file ingest is writing; ownership of the
  raw store is split across two processes.

### Option C: Each service prunes its own store

- The transform publishes its committed position into a table in the raw
  store. Ingest reads that position and prunes below it on its own cadence.
- Pros: one writer per store; raw stays small; the prune runs in the process
  that already owns the write connection.
- Cons: the position table is shared state between the two services, and the
  gap audit needs a coverage record that outlives the rows.

## Decision

**Option C.** The transform publishes its committed watermark; ingest reads it
and prunes below it on its own cadence. Each store keeps exactly one writer.

`gap_index` becomes the durable record of what was ingested. It already fills
incrementally (`INSERT ... WHERE pk > (SELECT MAX(pk) FROM gap_index)`), lives
in its own `index.db`, and persists across restarts, so front-pruning `events`
leaves it intact by construction. Coverage is recorded there before the
corresponding rows leave `events`.

## Consequences

- **`gap_index` becomes authoritative rather than derived.** Once `events` is
  a buffer, it is the only record that a range was ingested. Rebuilding it
  means re-backfilling, which succeeds inside Jetstream's roll-back window and
  leaves a permanent hole outside it.
- **`gap_index` needs its own prune.** It costs ~12 bytes per event — 86 MB at
  7.1M rows, ~260 MB per day. Rows below the retention floor cannot affect gap
  detection, so the floor is the prune condition. It is the third store with a
  retention rule and the one whose growth stays invisible from the UI.
- **The position table is a contract between two services.** Ingest prunes on
  what transform wrote. A stopped transform holds the prune and the raw store
  grows until it resumes, costing disk. A transform publishing a position
  ahead of its commit costs data.
- Raw-store steady state drops from ~15.6 GB to roughly one cycle's backlog,
  ~15k rows.
- The raw store's row count now measures buffer depth, which is what
  [ADR-0015](0015-tui-as-control-plane-client.md) accounts for.
