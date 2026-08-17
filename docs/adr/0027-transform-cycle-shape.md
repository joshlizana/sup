# ADR-0027: The transform cycle is sequential, chunked, and flushed per chunk

## Status

Accepted

## Options considered

- **A reader and a writer as concurrent tasks with a queue between them**,
  mirroring ingest. The queue holds a batch alive while it waits, and the
  watermark can only advance after the mart commit, so the two ends need a
  commit protocol between them.
- **One buffer for the whole backlog, one flush at the end.** A 24,322,991-row
  backlog reaches 9.9 GB of Arrow at the 408 bytes per row measured.
- **Validate the chunk into a list, then extract from it.** 115.5 ms per
  15,000 rows against the fused loop's 67.7 ms.
- **One sequential cycle over 100,000-row chunks, each flushed before the
  next read.** Chosen.

## Decision

One coroutine runs the cycle. It triggers on ~15,000 unconsumed rows
([ADR-0014](0014-mart-grain-and-transform-cadence.md)), then consumes
everything above the watermark in 100,000-row chunks, flushing each before
reading the next.

Within a chunk, each raw row is validated and flattened into its mart row in
one pass, so the model is unreachable once its tuple exists. Tuples bucket
by collection into five Arrow tables, which the insert reads as a registered
view ([ADR-0025](0025-connection-local-state-in-aioduckdb.md)).

The five inserts share one transaction, so a chunk reaches every collection
or none and each chunk is one DuckLake snapshot. The watermark advances
after that commit, which bounds a crash to replaying one chunk
([ADR-0023](0023-committed-position-in-sqlite.md),
[ADR-0026](0026-uniqueness-on-insert-with-a-two-column-hash-key.md)).

## Why

One 100,000-row chunk end to end, measured on real payloads and real event
keys against a 20,000,000-row mart:

| Stage | Cost |
|---|---:|
| SQLite chunk read | ~125 ms |
| validate and extract, fused | 456 ms |
| anti-join insert across five tables | 123.5 ms |
| commit | ~12 ms |
| total | ~717 ms, 139,000 rows/s |

Nothing in that sequence is worth overlapping. The longest stage holds the
GIL, and the two I/O stages together are a fifth of it, so concurrency buys
throughput a queue would give back by keeping a batch resident.

**Chunk size.** Fanning a chunk across five tables pays a fixed per-query
cost five times. A 15,000-row chunk takes 91.3 ms across the five and a
100,000-row chunk takes 123.5 ms: 6.09 µs per row against 1.24 µs, a 4.9x
improvement for the same work. Validation gives back 9% at the larger size,
219,145 rows/s against 241,179, because 100,000 live tuples give the
collector more to trace.

**The fused loop.** Holding a chunk's models alive costs 115.5 ms per 15,000
rows against the fused loop's 67.7 ms, and the gap is the garbage collector
tracing a live set of nested models: the same batch with collection disabled
costs 94.7 ms. Letting each model die as its tuple is built keeps the live
set near zero without touching the collector.

**Memory.** One chunk peaks at 209.9 MB above baseline and the second adds
22.7 MB, the third nothing — the allocator reuses the arenas. Draining
24,322,991 rows costs what draining one chunk costs, which is what makes
consuming everything above the watermark safe.

**Headroom.** Ingest sustains 28,983–35,218 rows/s through backfill and
209–276/s on the tail, so the transform runs about 4x the rate of the phase
that produces rows fastest, and 500x the tail.

Accepted costs:

- Validation is 63% of the drain, so the Python stage is the lever on
  backlog time.
- The first chunk of a run takes 883.8 ms against ~617 ms for later ones,
  paying allocation once.
- Each chunk is a DuckLake snapshot: 243 for a 24,322,991-row drain, and
  1,440 a day from a 60-second steady-state cycle, which
  `ducklake_expire_snapshots` clears
  ([ADR-0021](0021-service-maintenance.md)).
- A chunk's rows span about 100 minutes of event time, and a re-queued shard
  arrived 11.5 hours out of order, so a chunk reaches deep into the mart and
  the anti-join reads the whole destination table.
