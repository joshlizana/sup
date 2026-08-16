# TDD-0003: Ingest backfill and gap recovery

## Summary

On every start, `sup ingest` compares its last durably-recorded position
against Jetstream's current retention floor. The recoverable range is split
into time-ranged shards on a shared work queue ordered oldest-first, so the
ranges closest to aging out are claimed first. Range workers pull from that
queue and feed a single batched writer.

This is what fulfills M1's "bounded/documented loss window": bounded because
the worst case is computable from the retention floor, documented because the
bound follows from the design rather than from observation.

## Goals / Non-goals

**Goals:**

- Detect on every start whether a gap exists between the last-known position
  and now, and how much is recoverable.
- Backfill recoverable gaps automatically, prioritized by proximity to the
  retention floor, with the live tail claimed last (§5).

**Non-goals:**

- Cross-endpoint deduplication. Sharding assigns each worker a disjoint time
  range rather than reading one range from several endpoints.
- A durable record of permanently-lost ranges. Data past the retention floor
  is unrecoverable by definition and its absence is self-evident from the
  store. One case qualifies this: a write batch dropped after its retries are
  spent leaves a hole below §1's detection threshold, and that loss is
  accepted ([ADR-0010](../adr/0010-deduplication-in-the-mart.md)).

## Design

### 1. Gap detection, every start

- Read the last durably-recorded position from `gap_index`, a DuckDB table
  holding `(pk, time_us)` mirrored from `events` and advanced by
  `pk > MAX(pk)`. Gap detection is a windowed `LAG(time_us)` scan over it.
- Determine each endpoint's retention floor by connecting with `cursor=0`.
  Any cursor at or below the roll-back window behaves the same: the server
  starts at its oldest available message and streams forward. That is a full
  replay, not a lightweight probe, so the probe **reads until the first
  commit message, captures its `time_us`, and disconnects immediately**.
  Verified live: `cursor=0` yields a first commit roughly 24h old.
- Probe all six endpoints concurrently and take the oldest floor. A probe
  that fails or times out drops out of the comparison; the configured
  retention applies only if every probe fails.
- `Config.retention` is a **policy cap**, not merely a fallback — the floor
  used is the more recent of it and the oldest endpoint floor, so the window
  can be deliberately narrowed below what Jetstream still holds.
- Recoverable range = `[max(last_recorded_position, retention_floor), now)`.

### 2. Sharding

The recoverable range splits into 30-minute intervals, each widened by ten
seconds at both edges so coverage across a boundary is continuous. The
overlap delivers those events twice, which the mart resolves.

### 3. Queues — one priority work queue, plain FIFO message queues

- **The work queue** (`backfill_queue`) is a `PriorityQueue` of
  `(start, end)` ranges. Ordering by `start` is what makes workers claim the
  most-at-risk ranges first, and it is the only place ordering is
  load-bearing.
- **The message queues** (`output_queue`, `dlq_queue`) are plain FIFO. §7
  establishes that the mart's watermark is the autoincrement PK, so insertion
  order is irrelevant to correctness, and enforcing one would cost a heap
  comparison per message at firehose rate.

Both message queues are bounded at 10,000
([ADR-0009](../adr/0009-bounded-write-queues.md)), which bounds the crash-loss
window, caps each write batch, and paces readers to the writer.

Multiple readers, one writer, batching into the raw store
([ADR-0002](../adr/0002-raw-ingestion-durability.md)). The writer appends
without deduplicating
([ADR-0010](../adr/0010-deduplication-in-the-mart.md)).

### 4. Per-shard worker behavior

Each worker reads forward from its range's start and stops at its end. On
interruption it re-enqueues a *narrower* range, from its last processed
position to the original end.

A worker rejects any range starting below its own endpoint's retention floor
and returns it to the queue, since another endpoint may still serve it. Each
worker probes its floor once and caches it; backfill drains the window in
minutes, which bounds how stale that cache becomes.

That same probe measures the endpoint's witness lag — the message's `time_us`
against the `rev` it carries — and a reader whose endpoint exceeds ten
seconds claims no work for the rest of the run
([ADR-0019](../adr/0019-endpoint-witness-lag-screening.md)). A late-stamping
endpoint breaks two things: `time_us` is the cursor, so a claimed range
completes without being read, and the coverage §1 infers from stored `time_us`
is asserted at times nothing was ingested.

Workers extract `did` and `time_us` from the message, `rkey` and `rev` from
its `commit`, and `tid_us` by decoding `rev`
([ADR-0018](../adr/0018-event-time-from-commit-rev.md)), queueing the payload
verbatim beside them. Payloads that fail to parse go to the DLQ.

A message lacking any required column is dropped rather than stored, carrying
no record from a wanted collection. Measured over 207,439 consecutive live
messages that path is 0.16% — 181 `account` and 148 `identity` events. All
207,110 commits carried every column, so the malformed-commit case is
defensive rather than observed. The cursor still advances across a dropped
message.

Endpoints do not always honor a requested cursor, so a worker discards
messages outside its claimed range and never lets `current_cursor` move
backward ([ADR-0020](../adr/0020-live-tail-cursor-clamping.md)).

### 5. Live tail

The live tail is a range like any other: `(now, sys.maxsize)`, appended to the
work queue. Its `start` is the most recent value in the set, so the priority
queue hands it out **last**. That is intended — backfill ranges are the ones
racing the retention floor.

Deferring it costs no completeness. Its `start` is pinned at audit time, so
whenever it is claimed it resumes from that position and catches up. The only
visible effect is that a first run shows no live-tail activity until backfill
is substantially drained.

Chosen over adding fairness to the work queue, which would slow the urgent
work to make the live-tail number look busier.

### 6. TUI ingest-rate metric

Displays total messages/sec across all active connections, backfill and tail
combined, which keeps the figure representative while backfill dominates.

`Reader.update_throughput()` keeps a 10-second rolling window per worker and
`Ingester` sums them into its `status` reply. The TUI reads that over the
control plane rather than from the reader objects, which live in the ingest
subprocess ([ADR-0015](../adr/0015-tui-as-control-plane-client.md)).

The two rates differ by more than two orders of magnitude: the live service
produces 235–275 messages/sec across the five collections, while backfill
sustains tens of thousands. A full 24-hour window drains in around ten
minutes, and the live tail contributes well under 1% of the displayed figure
while backfill runs.

### 7. Watermark — safe against out-of-order writes

The mart's watermark ([TDD-0001](0001-architecture-overview.md) [3]) is the
raw store's autoincrement PK, not `time_us`. Advancement is
`pk > last_watermark` and every row gets a fresh strictly-increasing PK at
insert, so every row is picked up exactly once regardless of whether backfill
wrote older event-time data after newer live data.

One consequence for the mart schema: because backfill can insert older
event-time data after the mart processed newer data, a past period's counts
can appear incomplete and grow later. A dashboard-UX detail, not a
correctness bug.

## Open questions

- Shard size (30 min) is a starting default, not derived from a constraint.
  A full window drains in around ten minutes, so a shard is a small unit of
  re-work on interruption.
- **Resolved:** messages missing a required column are dropped rather than
  DLQ'd (§4).
- **Resolved:** message queues are bounded at 10,000
  ([ADR-0009](../adr/0009-bounded-write-queues.md)).
- **Resolved:** "last durably-recorded position" comes from `gap_index` (§1).
- **Resolved:** the raw store deduplicates nothing; the mart resolves
  re-deliveries ([ADR-0010](../adr/0010-deduplication-in-the-mart.md)).
