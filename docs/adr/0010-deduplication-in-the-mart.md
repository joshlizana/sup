# ADR-0010: Deduplicate in the mart, keep the raw store append-only

## Status

Accepted

## Options considered

- **Keep `UNIQUE(did, rkey, rev)` on the raw store.** It stays
  self-consistent, so anything reading it sees each event once. Caps the
  writer at ~37,000 rows/s to suppress ~0.5% of rows, and the cost grows with
  the index.
- **Replace the constraint with a hash column.** `UNIQUE` on a 64-bit hash of
  the triple. Index entries shrink roughly sevenfold so more of the B-tree
  stays cache-resident, but insertion is still random — the same curve,
  further out. Adds a derived column to maintain, and a collision silently
  drops a distinct event.
- **Shrink or remove the overlap buffer.** Addresses the wrong quantity.
  Every insert pays the random-write penalty whether or not it is a
  duplicate, so cutting duplicates to 0% leaves the 6x cost untouched while
  giving up boundary coverage.
- **Deduplicate in the mart, keep the raw store append-only.** Chosen.

## Decision

The writer appends unconditionally with a plain `INSERT`, and the M2
transform resolves duplicates on the way into DuckLake. `(did, rkey, rev)` is
the identity of an event and the transform picks one row per triple — a hard
requirement of M2, not an optimization.

The transform resolves them in two places, both on the insert: one row per
key within the chunk, covering the copies a shard overlap delivers together,
and an anti-join against the destination table, covering a chunk replayed
after a crash. Both key on the hash pair in
[ADR-0026](0026-uniqueness-on-insert-with-a-two-column-hash-key.md).

`pk INTEGER PRIMARY KEY AUTOINCREMENT` stays. It is sequential, so it carries
none of this cost, and the mart's watermark depends on it
([TDD-0003](../tdd/0003-ingest-backfill-and-gap-recovery.md) §7).

## Why

Backfill shards are widened by ten seconds at each edge
([TDD-0003](../tdd/0003-ingest-backfill-and-gap-recovery.md) §2) so coverage
across a boundary is continuous, which delivers those events twice —
about 0.5% of rows on a 30-minute shard. Absorbing them in the raw store was
the pipeline's throughput ceiling. Measured on real ingested rows, on the raw
store's disk, against a 3M-row base:

| Schema | Insert rate | Per 10k batch |
|---|---|---|
| With `UNIQUE(did, rkey, rev)` | ~37,000 rows/s | 271 ms |
| Without the unique index | ~221,000 rows/s | 45 ms |

A **6x penalty**. Varying one column family at a time isolates it:

| Varying | Insert rate | Cost |
|---|---|---|
| Sequential identifiers, sequential `time_us` | baseline | — |
| Sequential identifiers, random `time_us` | −10% | `idx_events_time_us` is nearly free |
| **Random identifiers**, sequential `time_us` | **−87%** | `UNIQUE(did, rkey, rev)` |

The mechanism is random insertion into a large B-tree. `did`, `rkey` and
`rev` are high-entropy text, so every insert touches an unpredictable leaf,
and past the point where the index stays cache-resident most inserts become
disk reads. The runtime profile matches — 65% of one core while inserts took
642 ms per 10k rows, waiting on I/O rather than starved of CPU. Building the
index is cheap and maintaining it is not: `CREATE UNIQUE INDEX` over 3M rows
completes in ~3 s as a sort, while inserting those same rows one batch at a
time *through* the index costs ~80 s. Size is not the issue either — the
index is ~210 MB of a 2.44 GB database.

So the constraint costs a 6x write penalty across every row to suppress ~0.5%
of them, and the trade worsens over time, since the penalty scales with index
size while the duplicate rate stays fixed. A hash column improves the
constant without changing the shape. Deduplication in DuckLake is one set
operation per transform run against a per-row random B-tree write on every
ingest, and it matches the split the architecture already draws: the raw
store captures what arrived, the mart decides what it means.

Measured after implementation on a fresh store, alongside `orjson` replacing
the stdlib parser and ingest-time validation removed:

| | Before | After |
|---|---|---|
| Durable write rate | 17.5k rows/s, degrading | ~29–35k rows/s, flat to 1.9M rows |
| Process CPU | 109% of one core | 66% |
| Write queue depth | pinned at its 10,000 cap | ~210 |
| Peak RSS | 1,137 MB | ~470 MB |
| Binding constraint | the writer | Jetstream endpoint capacity |

The end-to-end gain is ~1.7x rather than 6x, because removing the constraint
moved the bottleneck off the writer entirely. Insert cost is now flat with
table size, which matters more than the absolute figure for a store meant to
run continuously: two consecutive runs measured 30,010 and 29,116 rows/s
while the table doubled, where the previous schema lost roughly a third over
comparable growth.

Accepted costs:

- Anything reading the raw store directly sees duplicates.
- Validation moves to M2 alongside deduplication
  ([ADR-0011](0011-record-validation-and-routing-in-the-mart.md)), measured at
  271,511 rows/s — 21.6 s per 24-hour window.
- **The store keeps every copy it received, up to a failed write.** The
  writer retries a failing batch three times, each bounded by SQLite's
  5-second `busy_timeout`, then logs the size and drops it. A dropped batch
  spans about 5.6 ms at backfill rate and 22 ms live, against a p99.99 of
  99 ms for ordinary inter-event spacing — below the gap detector's floor, so
  the range is never re-fetched. M1 asks for a bounded, documented loss
  window; this is it.
- **The overlap rate is 0.56%**, measured over 27,048,844 rows across 24.24
  hours ([ADR-0020](0020-live-tail-cursor-clamping.md)). It is the overlap and
  nothing else: 99.44% of identities appear once, 0.560% appear exactly
  twice, and two rows in 27 million appear more often than that. Shards
  advance by thirty minutes plus ten seconds while each is widened ten
  seconds at its leading edge, so consecutive shards overlap by ten seconds
  — 480 s of double coverage in 86,400, or 0.556% predicted against 0.557%
  observed.
- Reverting is cheap: rebuilding the unique index over an existing table is a
  ~3 s sort, restorable offline.
- Revisit trigger: if the transform's deduplication step becomes the new
  bottleneck, the tradeoff has moved rather than resolved, and the hash
  column is the middle ground.
