# ADR-0013: Each service prunes its own store

## Status

Accepted

## Options considered

- **Raw keeps a full 24-hour window.** Prune raw on the retention floor
  rather than on mart commit: one retention rule for both layers and an
  unchanged gap audit, at ~37.4 GB of raw store as steady state, holding a
  second copy of what the mart already has in compressed Parquet.
- **Transform prunes what it consumed.** Raw stays small and the prune is
  trivially correct against the commit it follows, but the transform writes
  to the file ingest is writing, and ownership of the raw store splits across
  two processes.
- **Each service prunes its own store.** The transform publishes its
  committed position into a table in the raw store; ingest reads that
  position and prunes below it on its own cadence. One writer per store, raw
  stays small, and the prune runs in the process that already owns the write
  connection. The position table becomes shared state, and the gap audit
  needs a coverage record that outlives the rows. Chosen.

## Decision

The transform publishes its committed watermark; ingest reads it and prunes
below it on its own cadence. Each store keeps exactly one writer.

`gap_index` becomes the durable record of what was ingested. It already fills
incrementally (`INSERT ... WHERE pk > (SELECT MAX(pk) FROM gap_index)`),
lives in its own `index.db`, and persists across restarts, so front-pruning
`events` leaves it intact by construction. Coverage is recorded there before
the corresponding rows leave `events`.

## Why

The raw store is the product's largest disk consumer by a wide margin:
48,034,622 rows in 37.4 GB across a full continuous 24.5-hour window. Only
22,905,289 of those rows are distinct identities, so 46.19% are duplicates
([ADR-0010](0010-deduplication-in-the-mart.md)) and the figure reflects a
defect as much as a workload — resolving it roughly halves the number.

Having the transform delete raw rows would open a write connection to the
SQLite file ingest is writing, turning a read-only attachment into a second
writer on the same lock, which is the cross-process pattern
[ADR-0002](0002-raw-ingestion-durability.md) accepted as unvalidated.

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
- **`gap_index` needs its own prune.** It costs ~12 bytes per event — 86 MB
  at 7.1M rows and 733 MB at 48M, about 730 MB per day. Rows below the
  retention floor cannot affect gap detection, so the floor is the prune
  condition. It is the third store with a retention rule and the one whose
  growth stays invisible from the UI.
- **The position table is a contract between two services.** Ingest prunes on
  what transform wrote. A stopped transform holds the prune and the raw store
  grows until it resumes, costing disk. A transform publishing a position
  ahead of its commit costs data.
- Raw-store steady state drops from ~37.4 GB to roughly one cycle's backlog,
  ~15k rows, so its row count now measures buffer depth — which is what
  [ADR-0015](0015-tui-as-control-plane-client.md) accounts for.
