# ADR-0021: Maintenance shares the writer's connection

## Status

Accepted

## Options considered

- **Pause ingest for the prune.** Quiesce the readers and the writer over
  the existing control plane, prune against a drained service, resume. No
  contention, and it reuses machinery `sup clean` already relies on
  ([ADR-0008](0008-sup-clean-full-reset.md)). But a service pausing itself
  bypasses `Controller._pause`, so `status` reports it running while it is
  drained, and `Ingester.resume()` knows only that `paused` is True, not who
  set it — a maintenance resume would override an operator's pause. Each
  cycle also rebuilds every reader task.
- **A second connection to the raw store.** Simple and independent, but
  SQLite's write lock is per database, so the prune and the writer contend.
  `bulk_insert` retries three times against a 5-second `busy_timeout` and
  then drops the batch, which is
  [ADR-0010](0010-deduplication-in-the-mart.md)'s documented loss window
  firing for something that is not a fault.
- **Share the writer's connection, run between flushes.** One connection,
  nothing to contend for, and no pause. Chosen.

## Decision

`Maintenance` lives in the writer module and takes the `SQLiteClient` the
`Writer` already holds. `Writer.run()` calls it after its flush pass, so no
write is ever in flight while it runs. Each step is a separate method
committing its own transaction:

1. Mirror new `(pk, time_us)` into `gap_index`.
2. Read the transform's committed position from the mart
   ([ADR-0013](0013-service-owned-pruning.md)).
3. Delete `events` below that position.
4. `incremental_vacuum` to return the freed pages
   ([ADR-0002](0002-raw-ingestion-durability.md)).

Steps 1 and 3 are ordered: coverage reaches `gap_index` before the rows
leave `events`. The prune is one statement, since a pass deletes about a
minute of events once `events` is a working buffer.

A time check decides when a pass runs. Backfill keeps the queues occupied,
so triggering on an idle writer would starve the prune for the whole
catch-up.

## Why

One connection makes ADR-0013's "one writer per store" literal rather than a
convention, and there is no lock for the prune and the writer to contend
for. When a pass does take time, the bounded queues absorb it and the
readers suspend on a full `put()`
([ADR-0009](0009-bounded-write-queues.md)) — backpressure rather than a
dropped batch.

Running between flushes is what makes one transaction per method true.
`bulk_insert` is an `executemany` followed by a `commit`, so a concurrent
prune could land between them, and a rollback on either side would discard
the other's uncommitted work — for the writer, events already taken off the
queue and unrecoverable.

Committing per step also makes an interrupted pass resumable. The mirror
continues from the highest `pk` already recorded and the prune from the
position the transform published, both of which survive a crash.

Accepted costs:

- `gap_index` gains its writer here, and `GapAuditor` keeps only the scan it
  runs at startup.
- Maintenance holds three handles: the writer's SQLite connection, DuckDB for
  `gap_index`, and a DuckLake read for the committed position.
- A stopped transform holds the prune and the raw store grows until it
  resumes, which ADR-0013 already accepts.
- Reclaiming space needs `auto_vacuum=INCREMENTAL`, which SQLite accepts only
  on an empty database, so a store built without it keeps its pages whatever
  the prune deletes.
