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
  then drops the batch, spending
  [ADR-0010](0010-deduplication-in-the-mart.md)'s documented loss window on
  lock contention.
- **Share the writer's connection, run between flushes.** One connection,
  nothing to contend for, and no pause. Chosen.

## Decision

Ingest and the transform each run a `Maintenance` instance against the stores
they own ([ADR-0013](0013-service-owned-pruning.md)). An instance takes the
connection its service already writes through and runs after that service's
write pass, so no write is in flight while it runs. Each step is a separate
method committing its own transaction.

Ingest's instance lives in the writer module and takes the `SQLiteClient` the
`Writer` already holds, called from `Writer.run()` after its flush pass:

1. Mirror new `(pk, time_us)` into `gap_index`.
2. Read the transform's committed position from the mart (ADR-0013).
3. Delete `events` below that position.
4. `incremental_vacuum` to return the freed pages
   ([ADR-0002](0002-raw-ingestion-durability.md)).
5. Delete `gap_index` rows below the retention floor (ADR-0013).

Steps 1 and 3 are ordered: coverage reaches `gap_index` before the rows
leave `events`. The prune is one statement, since a pass deletes about a
minute of events once `events` is a working buffer.

The transform's instance takes the DuckLake connection it writes the mart
through, called after a cycle commits, and purges past `Config.retention`
([ADR-0012](0012-rolling-retention-window.md)):

1. `DELETE` mart rows below the floor.
2. `ducklake_expire_snapshots`.
3. `ducklake_cleanup_old_files`.

A time check decides when either pass runs. On the ingest side backfill keeps
the queues occupied, so triggering on an idle writer would starve the prune
for the whole catch-up. On the transform side a cycle commits every ~15,000
rows ([ADR-0014](0014-mart-grain-and-transform-cadence.md)), and expiring
snapshots on each one spends file rewrites on a window that moves in
seconds.

## Why

One connection satisfies ADR-0013's "one writer per store" exactly, and
leaves no lock for the prune and the writer to contend for. A pass that
takes time fills the bounded queues and suspends the readers on `put()`
([ADR-0009](0009-bounded-write-queues.md)).

The three options above are the ones the transform faces against the mart,
and they resolve the same way: a service that pauses itself to prune
misreports its own `status`, a second connection contends with the writer it
belongs beside, and the connection already open for writing has neither
problem. One component covers both because the shape of the problem is
ADR-0013's invariant, not anything specific to SQLite.

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
- Ingest's instance holds three handles: the writer's SQLite connection,
  DuckDB for `gap_index`, and a DuckLake read for the committed position.
  The transform's holds one.
- The two instances share a shape and no steps. Ingest prunes against a
  position another service published; the transform prunes against a clock.
- A stopped transform holds the prune and the raw store grows until it
  resumes, which ADR-0013 already accepts.
- Reclaiming space needs `auto_vacuum=INCREMENTAL`, which SQLite accepts only
  on an empty database, so a store built without it keeps its pages whatever
  the prune deletes.
