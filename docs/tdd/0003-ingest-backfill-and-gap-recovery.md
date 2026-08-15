# TDD-0003: Ingest backfill and gap recovery

## Summary

On every start, `sup ingest` proactively compares its last durably-recorded
position against Jetstream's current retention floor. The recoverable range
is split into time-ranged shards and placed on a shared work queue, ordered
oldest-first so the ranges closest to aging out of retention get claimed
first. Range workers pull from that queue and feed a single writer, which
commits to the raw store in batches.

This is what actually fulfills M1's acceptance criterion ("bounded/documented
loss window") precisely: bounded, because the worst case is computable from
the retention floor; documented, because that bound follows from the design
rather than from observation after the fact.

## Goals / Non-goals

**Goals:**
- Detect, on every start, whether there's a gap between last-known position
  and now, and how much of it is recoverable.
- Backfill recoverable gaps automatically, prioritized by urgency (proximity
  to the retention floor), with the live tail deliberately claimed last —
  see §5.

**Non-goals:**
- Cross-endpoint deduplication. Sharding assigns each parallel worker a
  disjoint time range rather than reading the same range redundantly from
  multiple endpoints, so there's nothing to deduplicate.
- A durable record of permanently-lost ranges. Data past the retention
  floor is unrecoverable by definition, and its absence is self-evident
  from the store on any subsequent run — a separate record of what was
  missed would be redundant state to keep correct for no operational gain.
  One case qualifies this: a write batch dropped after its retries are
  spent leaves a hole below §1's detection threshold, and that loss is
  accepted. [ADR-0010](../adr/0010-deduplication-in-the-mart.md) records
  the bound and the alternatives weighed.

## Design

### 1. Gap detection, every start

- Read the last durably-recorded position: `MAX(time_us)` among rows
  already in the raw SQLite store (exact query depends on M1's schema,
  not yet finalized).
- Determine the retention floor per endpoint: connect with `cursor=0` (any
  cursor at or below the roll-back window behaves the same — Jetstream
  starts at the oldest available message and streams forward from there).
  This does **not** return a single lightweight probe response: the server
  begins replaying the entire roll-back window. So the probe must **read
  until the first commit message to capture its `time_us`, then disconnect
  immediately** — staying connected would turn the probe into an
  accidental full-backlog replay, redundant with the real sharded backfill
  workers. Verified against the live service: connecting at `cursor=0`
  yields a first commit roughly 24h old, matching Jetstream's roll-back
  window.
- Do this per configured endpoint (six, see `config.py`) concurrently, and
  take the most generous (oldest) floor across all of them. A probe that
  fails or times out drops out of that comparison rather than failing the
  audit — a single unreachable endpoint is survivable, and the configured
  default retention applies only if no endpoint can be probed at all.
- The configured default (`Config.retention`) acts as a **policy cap**, not
  merely a fallback: the floor used is the more recent of it and the oldest
  endpoint floor, so the window can be deliberately narrowed below what
  Jetstream still holds.
- Recoverable range = `[max(last_recorded_position, retention_floor), now)`.
- Anything older than the retention floor is unrecoverable and simply not
  attempted (see Non-goals).

### 2. Sharding

The recoverable range is split into fixed intervals — 30 minutes by
default, tunable — for parallelism. Each shard is assigned to its own
connection/reader.

### 3. Queues — one priority work queue, plain FIFO message queues

Two distinct kinds of queue, doing different jobs:

- **The work queue** (`backfill_queue`) is a `PriorityQueue` of
  `(start, end)` `time_us` ranges. Ordering it by `start` is what makes
  workers claim the oldest, most-at-risk ranges first, and it is the only
  place ordering is load-bearing.
- **The message queues** (`output_queue`, `dlq_queue`) are plain FIFO
  `asyncio.Queue`s feeding the single writer. Ordering messages by
  `time_us` before writing would buy nothing: §7 establishes that the mart
  watermark is the raw store's autoincrement PK, so insertion order is
  irrelevant to correctness, and enforcing an order the writer doesn't need
  would cost a heap comparison per message at firehose rate.

Both message queues are bounded at 10,000
([ADR-0009](../adr/0009-bounded-write-queues.md)), which bounds the
crash-loss window, caps each write batch, and paces readers to the writer's
rate when the writer falls behind.

Multiple readers, one writer, committing to the raw SQLite store in batches
per [ADR-0002](../adr/0002-raw-ingestion-durability.md). The writer appends
without deduplicating; the mart resolves duplicates
([ADR-0010](../adr/0010-deduplication-in-the-mart.md)).

### 4. Per-shard worker behavior

Each shard worker reads forward from its assigned range's start, stopping
when it reaches the assigned range's end. On interruption (pause,
disconnect), it re-enqueues a *narrower* range — from wherever it last
successfully processed to the shard's original end.

A worker rejects any range starting below its own endpoint's retention
floor and returns it to the queue, since another endpoint may still serve
it. Each worker probes its endpoint's floor once and caches the result,
falling back to `Config.retention` when the probe fails. Backfill drains
the recoverable window in minutes, which bounds how stale a cached floor
can become.

Workers extract the raw store's columns — `did` and `time_us` from the
message, `rkey` and `rev` from its `commit` — and queue the payload
verbatim beside them. Payloads that fail to parse go to the DLQ.

### 5. Live tail

The live tail is a range like any other: `(now, sys.maxsize)`, appended to
the work queue alongside the backfill shards. Because its `start` is the
most recent value in the set, the priority queue hands it out **last** —
after every backfill shard has been claimed. That is the intended
behavior, not an accident of the ordering key: the backfill ranges are the
ones racing the retention floor, and the live tail is not, so recoverable
history takes precedence over current traffic.

Deferring the tail costs nothing in completeness. Its `start` is pinned at
audit time, so whenever the worker actually claims it, it resumes from that
pinned position and catches up through everything that arrived while it
waited. The only visible effect is that a first run against an empty store
shows no live-tail activity until backfill is substantially drained.

### 6. TUI ingest-rate metric

Displays total messages/sec across *all* active connections (backfill
shards + live tail combined). This is what keeps the TUI representative
while backfill dominates throughput.

`Reader.update_throughput()` keeps a 10-second rolling window per worker, and
`Ingester` sums them into its `status` reply. The TUI reads that figure over
the control plane rather than from the reader objects, which live in the
ingest subprocess ([ADR-0015](../adr/0015-tui-as-control-plane-client.md)).

The two rates differ by more than two orders of magnitude. Measured across the
five configured collections over the sampled hours with near-complete
coverage, the live service produces **235–275 messages/sec** (median 250),
while the ingest pipeline sustains **tens of thousands per second** — so a
full 24-hour recoverable window (~21.6M events) drains in around ten minutes,
and the live tail contributes well under 1% of the displayed figure once
backfill is running.

Chosen over adding fairness/interleaving to the work queue itself, which
would slow down the thing that's actually supposed to be urgent just to make
the live-tail number look busier.

### 7. Watermark — confirmed safe, unrelated to insertion order

The mart transform's watermark ([TDD-0001](0001-architecture-overview.md)
[3]) is the raw store's autoincrement PK, not the message's `time_us`.
Since advancement is `pk > last_watermark` and every row gets a fresh,
strictly-increasing PK at insert time, every row is picked up by some
future transform run exactly once — regardless of whether it was inserted
"late" because backfill caught up on older data after newer live data was
already written. No duplicate- or missed-row risk from out-of-order
(event-time vs. insertion-time) writes.

One unrelated, minor consequence worth remembering whenever the mart schema
is actually designed (still TBD): because backfill can insert older
event-time data after the mart already processed newer live data, a past
time period's aggregated counts could temporarily appear incomplete in the
mart and grow later as backfill catches up on that period. Not a
correctness bug — a dashboard-UX detail for later.

## Open questions

- Exact shard size (30 min) is a reasonable starting default, not derived
  from a specific constraint. Observed throughput leaves it comfortable: a
  full recoverable window drains in around ten minutes, so a shard is a
  small unit of re-work on interruption.
- A commit message missing one of the four columns the raw store requires
  is currently skipped without reaching the DLQ, which leaves a loss path
  that nothing records. Deciding this is M1 work.
- **Resolved:** the message queues feeding the writer are bounded at
  `maxsize=10,000` each, bounding the crash-loss window and pacing readers
  to the writer. See [ADR-0009](../adr/0009-bounded-write-queues.md).
- **Resolved:** "last durably-recorded position" is derived from
  `gap_index`, a DuckDB table holding `(pk, time_us)` mirrored from the raw
  store's `events` table and advanced by `pk > MAX(pk)`. Gap detection runs
  as a windowed `LAG(time_us)` scan over it.
- **Resolved:** the raw store deduplicates nothing. Overlap-buffer
  re-deliveries are stored and resolved by the mart transform, at a
  measured ~2% duplicate share. See
  [ADR-0010](../adr/0010-deduplication-in-the-mart.md).
