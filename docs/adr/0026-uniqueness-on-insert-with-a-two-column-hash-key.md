# ADR-0026: Uniqueness is enforced on insert, keyed by two hash columns

## Status

Accepted

## Options considered

- **Anti-join on `(did, rkey, rev)`.** No extra column. 224.1 ms per
  15,000-row chunk against a 20M-row table.
- **Concatenated `did||rkey||rev` as `VARCHAR`.** Exact by construction.
  166.1 ms and 32.2 bytes per row.
- **One 64-bit hash.** 43.0 ms and 8 bytes per row, with a 1-in-90,000
  chance that some pair of the mart's keys collides and drops an event.
- **One 128-bit hash column.** DuckDB writes `HUGEINT` and `UHUGEINT` to
  Parquet as `DOUBLE`, which fabricated 200,000 collisions over 20,000,000
  rows.
- **Two 64-bit hash columns.** Chosen.

## Decision

Every mart table carries `k1 UBIGINT NOT NULL` and `k2 UBIGINT NOT NULL`,
holding a hash of `did||rkey||rev` and a hash of the same string under a
fixed salt. The pair is a 128-bit fingerprint in two columns DuckDB writes
to Parquet as `INT64`/`UINT_64`.

Each chunk lands through one `INSERT ... SELECT` per collection that reads
the registered Arrow table, computes both keys in DuckDB, keeps one row per
key pair within the chunk, and anti-joins on `p.k1=s.k1 AND p.k2=s.k2`
against the destination table. The five inserts share one transaction, so a
chunk reaches every collection or none, and the cycle produces one DuckLake
snapshot.

The salt and both hash expressions are fixed for the life of a mart. Rows
written under one definition are invisible to an anti-join running under
another.

The transform commits the mart and advances the watermark second
([ADR-0023](0023-committed-position-in-sqlite.md)). A crash between the two
replays the chunk, and the anti-join absorbs it, which is the mechanism
[ADR-0010](0010-deduplication-in-the-mart.md) relies on for the cross-cycle
case.

## Why

Measured on 20,000,000 real event keys from the raw store, inserting a
15,000-row chunk:

| Key | Anti-join | Table on disk |
|---|---:|---:|
| `(did, rkey, rev)` | 224.1 ms | 1063 MB |
| `VARCHAR` concatenation | 166.1 ms | 1707 MB |
| one `UBIGINT` | 43.0 ms | 1063 MB |
| two `UBIGINT` | 53.2 ms | 1223 MB |

The concatenation is unique per row and compresses poorly, costing 32.2
bytes per row against the pair's 8. A single 64-bit hash saves 10 ms and
buys a probability an exact scheme avoids: across 19,559,726 distinct real
keys the expected number of colliding pairs is about 10⁻⁵, and a collision
silently discards a valid event rather than raising. The second column takes
that to 128 bits, where the expectation is around 10⁻²⁴.

Three hash schemes were checked against the real key set for pathological
distribution. All three produced 19,559,726 distinct values from 19,559,726
distinct keys.

A single 128-bit column is unavailable. `md5_number` returns `UHUGEINT`, and
every one of 20,000,000 values read back differently after a round trip,
with distinct values falling to 19,359,726 — 200,000 events an anti-join
would drop. The mechanism is in
[TDD-0004](../tdd/0004-mart-schema.md).

Computing both keys inside the `INSERT ... SELECT` runs at 27.0 ms per
100,000 rows. The cheapest Python route, one md5 split into halves, costs
49.4 ms before the insert starts, and Python's built-in `hash` is salted per
process, so a key written by one run matches nothing written by another.
Keeping the expression in SQL puts the key's definition beside the schema
and produces both sides of the join from one code path.

Bounding the anti-join with `time_us` prunes little. A pk-contiguous chunk
spans about 100 minutes of event time because backfill shards interleave,
and a re-queued shard arrived 11.5 hours out of order three minutes after
the tail caught up, so a chunk's minimum `time_us` reaches far back into the
mart.

Accepted costs:

- 16 bytes per row across every mart table, 1223 MB against 1063 MB at
  20,000,000 rows.
- The anti-join grows with the mart: 19.6 ms at 5M rows and 35.8 ms at 20M
  for a 15,000-row chunk. A 24-hour window holds it near 20M
  ([ADR-0012](0012-rolling-retention-window.md)).
- Fanning one chunk across five tables costs five hash builds. A
  15,000-row chunk takes 91.3 ms across five tables against 53.2 ms into
  one, and a 100,000-row chunk takes 123.5 ms — 1.24 µs per row against
  6.09 µs at the smaller size.
- Uniqueness rests on a probability rather than a constraint. DuckLake
  rejects `PRIMARY KEY` and `UNIQUE`, so nothing in the schema catches a
  violation.
