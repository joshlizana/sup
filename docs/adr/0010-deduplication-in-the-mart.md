# ADR-0010: Deduplicate in the mart, keep the raw store append-only

## Status

Accepted

## Context

Backfill shards are widened by ten seconds at each edge
([TDD-0003](../tdd/0003-ingest-backfill-and-gap-recovery.md) §2) so that
coverage across a shard boundary is continuous. Events inside those overlap
windows are therefore delivered twice. Until now the raw store absorbed
them: `events` carried `UNIQUE (did, rkey, rev)` and the writer used
`INSERT OR IGNORE`, so a re-delivered event landed as an ignored duplicate.

That constraint turned out to be the ingest pipeline's throughput ceiling.
Measured against real ingested rows (real DIDs, rkeys, revs and payloads),
on the same disk the raw store lives on, with a 3M-row base so the index is
realistically sized:

| Schema | Insert rate | Per 10k batch |
|---|---|---|
| With `UNIQUE(did, rkey, rev)` | ~37,000 rows/s | 271 ms |
| Without the unique index | ~221,000 rows/s | 45 ms |

A **6x penalty**. Isolating which index is responsible, by varying one
column family at a time on a fixed table:

| Varying | Insert rate | Cost |
|---|---|---|
| Sequential identifiers, sequential `time_us` | baseline | — |
| Sequential identifiers, random `time_us` | −10% | `idx_events_time_us` is nearly free |
| **Random identifiers**, sequential `time_us` | **−87%** | `UNIQUE(did, rkey, rev)` |

The mechanism is random insertion into a large B-tree. `did`, `rkey` and
`rev` are high-entropy text, so every insert touches an unpredictable leaf;
past the point where the index stays resident in page cache, most inserts
become disk reads. This matches the observed runtime profile — the process
sat at 65% of a single core while inserts took 642 ms per 10k rows, i.e.
waiting on I/O rather than starved of CPU.

Two further measurements shaped the decision:

- **Building the index is cheap; maintaining it is not.** `CREATE UNIQUE
  INDEX` over 3M existing rows completes in ~3 seconds as a sort. Inserting
  those same rows one batch at a time *through* the index costs ~80 seconds.
- **Size on disk is not the issue.** The index accounts for ~210 MB of a
  2.44 GB database. Random access across even that much B-tree is enough to
  defeat page cache under sustained writes.

Volume of duplicates at stake: a 10-second overlap on a 30-minute shard is
roughly **0.5%** of rows.

## Options considered

### Option A: Keep `UNIQUE(did, rkey, rev)` in the raw store

- Status quo: the raw store is the deduplication point.
- Pros: the raw store is self-consistent, so anything reading it — the TUI,
  an ad-hoc query, the mart transform — sees each event once with no further
  work.
- Cons: caps the writer at ~37,000 rows/s, six times below what the same
  hardware sustains without it, and the cost grows as the index does. Spends
  that on suppressing ~0.5% of rows.

### Option B: Replace the constraint with a hash column

- Store a 64-bit hash of `(did, rkey, rev)` and put `UNIQUE` on that.
- Pros: same semantics; index entries shrink roughly sevenfold, so far more
  of the B-tree stays cache-resident.
- Cons: reduces the penalty rather than removing it — insertion is still
  random, so the same curve applies, just further out. Adds a derived column
  whose correctness has to be maintained, and a hash collision would silently
  drop a distinct event.

### Option C: Deduplicate in the mart, keep the raw store append-only

- Drop the unique constraint. The writer appends unconditionally, and the
  M2 transform resolves duplicates on the way into DuckLake.
- Pros: removes the penalty entirely, taking the writer's ceiling to
  ~221,000 rows/s. Deduplication becomes a set operation in a columnar
  engine built for them, applied once per transform run rather than once per
  row. Fits the layering already described in
  [TDD-0001](../tdd/0001-architecture-overview.md): the raw store is durable
  capture, the mart is where transformation happens.
- Cons: the raw store now contains duplicates, so anything reading it
  directly must account for them. Correctness moves to the transform, which
  has to be right for the mart to be right.

### Option D: Shrink or remove the overlap buffer

- Reduce duplicates at the source.
- Cons: addresses the wrong quantity. Every insert pays the random-write
  penalty whether or not it turns out to be a duplicate, so cutting the
  duplicate rate from 0.5% to 0% leaves the 6x cost untouched — while giving
  up the boundary coverage the buffer exists to guarantee.

## Decision

**Option C — the raw store is append-only; the mart deduplicates.**

The deciding factor is the ratio. The constraint costs a 6x write penalty
across every row in order to suppress ~0.5% of them, and that trade gets
worse over time: the penalty scales with index size while the duplicate rate
stays fixed. Option B improves the constant without changing the shape.

It also lands the work where it is cheapest. Deduplication in DuckLake is a
single set operation over a batch, in a columnar engine, once per transform
run — against a per-row random B-tree write on every ingest. And it matches
the responsibility split the architecture already draws: the raw store
captures what arrived, the mart decides what it means.

The raw store keeps `pk INTEGER PRIMARY KEY AUTOINCREMENT`. It is sequential
rather than random, so it carries none of this cost, and the mart's
watermark depends on it
([TDD-0003](../tdd/0003-ingest-backfill-and-gap-recovery.md) §7).

## Consequences

Measured after implementation, on a fresh store, alongside `orjson` in place
of the stdlib JSON parser and ingest-time Pydantic validation removed:

| | Before | After |
|---|---|---|
| Durable write rate | 17.5k rows/s, degrading as the table grew | ~29–35k rows/s, flat from 0 to 1.9M rows |
| Process CPU | 109% of one core (saturated) | 66% |
| Write queue depth | pinned at its 10,000 cap | ~210 |
| Peak RSS | 1,137 MB | ~470 MB |
| Binding constraint | the writer | Jetstream endpoint capacity |

- The end-to-end gain is ~1.7x rather than the ~6x the isolated benchmark
  suggested, because removing the constraint moved the bottleneck off the
  writer entirely. The queue now drains in real time and the pipeline is
  limited by what the six public endpoints serve (~37k msg/s aggregate).
- **Insert cost is now flat with table size**, which matters more than the
  absolute figure for a store meant to run continuously. Two consecutive
  runs measured 30,010 and 29,116 rows/s while the table doubled; the
  previous schema lost roughly a third over comparable growth.
- Measured duplicate share in the raw store is **1.9%** (5,749,643 distinct
  of 5,859,179 rows), against the ~0.5% predicted from overlap buffers alone
  — the remainder came from repeated test runs re-reading ranges. That is
  the volume the mart's deduplication step has to absorb.
- `INSERT OR IGNORE` in `writer.py` has no constraint left to act on and
  becomes a plain `INSERT`. Keeping the `OR IGNORE` form would silently
  suppress genuine failures.
- **The M2 transform owns deduplication and must be correct for it.**
  `(did, rkey, rev)` remains the identity of an event; the transform picks
  one row per triple on the way into DuckLake. This is now a hard
  requirement of M2 rather than an optimization.
- Validation moved to M2 along with deduplication.
  [ADR-0011](0011-record-validation-and-routing-in-the-mart.md) settles how:
  Pydantic, using `models.py`'s discriminated union to extract
  per-collection columns, with routing keyed on `commit.collection`.
  Measured at 271,511 rows/s over the full raw store, so the round trip out
  of the engine and back costs 21.6 seconds per 24-hour window.
- **The store keeps every copy it received, up to a failed write.** The
  writer retries a failing batch three times, each bounded by SQLite's
  5-second `busy_timeout`, then logs the batch size and drops it. A dropped
  batch spans about 5.6 ms at backfill rate and 22 ms at the live rate,
  against a p99.99 of 99 ms for ordinary inter-event spacing over 5.86M
  rows — below the gap detector's floor, so the range is never re-fetched.
  Stalling the pipeline until the write lands, and spilling failed batches
  to a file, were both weighed; contention outlasting ~15 seconds sits
  outside expected operation, and a live firehose drops messages by nature.
  M1 asks for a bounded, documented loss window, and this is it.
- Anything reading the raw store directly sees duplicates, so the TUI's row
  counts and ingest rate read slightly high — acceptable for a health view.
- Overlap buffers account for ~0.5% of duplicates; a crash and restart adds
  more, re-reading from the last durably recorded position so any events
  written but absent from the gap index arrive again. The same batching
  window bounds it.
- Reverting is cheap: rebuilding the unique index over an existing table is
  a ~3 second sort, restorable offline without reprocessing.
- Revisit trigger: if the mart transform's deduplication step becomes the
  new bottleneck, the tradeoff has simply moved rather than resolved, and
  the hash-column variant (Option B) is the middle ground to reconsider.
