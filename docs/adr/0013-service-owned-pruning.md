# ADR-0013: Each service prunes its own store

## Status

Accepted

## Options considered

- **Raw keeps a full 24-hour window.** Prune raw on the retention floor
  rather than on mart commit: one retention rule for both layers and an
  unchanged gap audit, at ~21 GB of raw store as steady state, holding a
  second copy of what the mart already has in compressed Parquet.
- **Transform prunes what it consumed.** Raw stays small and the prune is
  trivially correct against the commit it follows, but the transform writes
  to the file ingest is writing, and ownership of the raw store splits across
  two processes.
- **Each service prunes its own store.** The transform publishes its
  committed position where it is the only writer; ingest reads it and prunes
  below it on its own cadence. One writer per store, raw stays small, and the
  prune runs in the process that already owns the write connection. The
  position becomes shared state, and the gap audit needs a coverage record
  that outlives the rows. Chosen.

## Decision

The transform publishes its committed watermark into `watermark.db`, its own
SQLite store ([ADR-0023](0023-committed-position-in-sqlite.md)); ingest reads
it through `aiosqlite` and prunes below it on its own cadence. Each store
keeps exactly one writer.

`gap_index` becomes the durable record of what was ingested. It already fills
incrementally (`INSERT ... WHERE pk > (SELECT MAX(pk) FROM gap_index)`),
lives in its own `index.db`, and persists across restarts, so front-pruning
`events` leaves it intact by construction. Coverage is recorded there before
the corresponding rows leave `events`.

## Why

The raw store is the product's largest disk consumer by a wide margin:
27,048,844 rows in 21.1 GB across a continuous 24.24-hour window, at 780
bytes per row. Almost all of it is real — 0.56% duplicates, which is the
shard overlap ([ADR-0010](0010-deduplication-in-the-mart.md)) — and it
accumulates for as long as nothing prunes it.

Having the transform delete raw rows would open a write connection to the
SQLite file ingest is writing, making it a second writer on the same lock.
[ADR-0002](0002-raw-ingestion-durability.md) keeps one writer per store, and
the transform's own reads are already the delicate half of that boundary.

Pruning `events` to a buffer also breaks the audit. TDD-0003 §1 derives
ingest's position by scanning `events` on every start, and ingest holds no
separate cursor state ([TDD-0002](../tdd/0002-cli-orchestration.md)), so a
front-pruned table would leave the audit reading a nearly-empty store and
re-backfilling the window on every restart. Moving the durable coverage
record to `gap_index` is what makes the prune safe.

Accepted costs:

- **`gap_index` becomes authoritative rather than derived.** Once `events` is
  a buffer, it is the only record that a range was ingested. Rebuilding it
  means re-backfilling, which succeeds inside Jetstream's roll-back window
  and leaves a permanent hole outside it.
- **`gap_index` needs its own prune.** It costs 12 to 15 bytes per event —
  86 MB at 7.1M rows, 733 MB at 48M — so a day of ingest is roughly 400 MB
  at current volume. This is a disk concern rather than a time one: the
  windowed `LAG` over it runs in 0.26 s at 29M rows. Rows below the
  retention floor cannot affect gap detection, so the floor is the prune
  condition. It is the third store with a retention rule and the one whose
  growth stays invisible from the UI.
- **The position table is a contract between two services.** Ingest prunes on
  what transform wrote. A stopped transform holds the prune and the raw store
  grows until it resumes, costing disk. A transform publishing a position
  ahead of its commit costs data.
- Raw-store steady state drops from ~21 GB to roughly one cycle's backlog,
  ~15k rows, so its row count now measures buffer depth — which is what
  [ADR-0015](0015-tui-as-control-plane-client.md) accounts for.
