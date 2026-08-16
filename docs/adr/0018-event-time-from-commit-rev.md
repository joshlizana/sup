# ADR-0018: Event time comes from `commit.rev`

## Status

Accepted

## Options considered

Three timestamps reach the raw store, and measurement separates them sharply.

- **`time_us`.** What ingest already stores and shards on. Bluesky documents
  it as "the operator-imported `indexed_at` value if one was set, otherwise
  the `witnessed_at` time Jetstream first saw the event" — explicitly not the
  record's creation time. It is a property of the instance that emitted the
  message, so the same event carries different values on different endpoints,
  and one endpoint stamped 4,291,546 rows a median of 9.6 hours after their
  commit ([ADR-0019](0019-endpoint-witness-lag-screening.md)).
- **`record.createdAt`.** Client-supplied. Across 200,001 sampled rows it
  sits a median of 146 ms before the commit, which is plausible, but 1.73%
  differ by over a minute and 0.37% by over an hour, with extremes 24 years
  in the past and 56 in the future. It is also absent from 3.78% of rows —
  exactly the delete rate, since deletes carry no record.
- **`commit.rev`.** A TID the PDS assigns when it writes the commit: 13
  characters of base32-sortable text encoding 53 bits of microseconds and a
  10-bit clock identifier. Present on every row including deletes, and
  decoded within 8 ms of the record's own `rkey` on the row inspected.
  Chosen.

## Decision

**Event time is `commit.rev`, decoded.** `sup.util.tid_us` performs the
decode and ingest stores the result as the `tid_us` column, so the mart reads
a plain integer rather than repeating the decode.

`time_us` keeps its existing job as the ingest cursor and nothing more.
`createdAt` stays available as a column: "when the author claims they posted"
is content worth querying, distinct from when the network recorded it.

## Why

The mart plots activity over time
([ADR-0014](0014-mart-grain-and-transform-cadence.md)), so it needs one
timestamp per event that means the same thing on every row. `time_us` fails
that by construction, since it describes the endpoint rather than the event,
and `createdAt` is both client-controlled and missing from every delete.
`commit.rev` is assigned by the PDS at write time, exists on every row, and
tracks the record's own `rkey` closely where both are present.

Accepted costs:

- **A plausibility guard is required.** 0.2351% of rows decode to timestamps
  outside 2020-2030, clustering near the epoch — 71,843 rows across 8,474
  distinct DIDs on a later 27M-row store, so it is a class of client
  implementation rather than a few accounts. Every one carries a well-formed
  13-character `rev`, and values run consecutively across a sub-second span,
  so those implementations mint `rev` as a counter rather than a clock. The
  decode cannot detect this; only a range check can.
- **The guard has a live consumer before the mart.**
  [ADR-0019](0019-endpoint-witness-lag-screening.md)'s probe compares a
  message's `time_us` against `tid_us(rev)`, so a probe landing on a
  counter-minted `rev` misses the threshold by nine orders of magnitude and
  benches a healthy endpoint for the run.
- **`tid_us` is on the write path for every row.** A `rev` outside the
  alphabet raises inside `process_message`'s `try`, which sends an otherwise
  valid message to the DLQ. All 48,034,622 rows measured carry exactly 13
  characters, so this is untriggered rather than impossible.
- **`rev` is the PDS's clock, not a network clock.** 79 rows carry a `tid_us`
  later than the `time_us` that observed them, clustered near −98 s, which is
  a single PDS running fast. Causally an event is committed before it is
  witnessed; across independent machines the arithmetic can still invert, so
  comparisons against `time_us` keep their sign rather than taking an
  absolute value.
- Deletes gain a usable timestamp, which `createdAt` could never give them.
- The mart's time axis stops depending on which endpoint delivered a row,
  making per-collection volume comparable across the pool.
