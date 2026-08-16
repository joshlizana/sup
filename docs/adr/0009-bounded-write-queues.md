# ADR-0009: Bounded in-memory write queues

## Status

Accepted

## Options considered

- **Unbounded.** Readers never block, but the crash-loss window has no
  stateable ceiling, memory grows whenever the writer lags, and a slow writer
  stays invisible until the process is swapping.
- **Bounded at 1,000.** The loss window becomes a computable constant and
  `put()` suspending gives backpressure for free, with memory pinned near
  1 MB per queue.
- **Bounded at 10,000.** The same mechanism with an order of magnitude more
  slack, absorbing longer stalls before backpressure engages, at ~10 MB per
  queue. Chosen.

## Decision

**Both queues bounded at `maxsize=10,000`** — `output_queue` for events and
`dlq_queue` for payloads that failed to parse.

The `maxsize` also satisfies the count half of the batched writer's "N
messages or X ms" requirement without a separate counter, since the writer
drains everything available on each pass.

## Why

These two `asyncio.Queue`s are the only thing between "received from
Jetstream" and "durable in SQLite". M1's acceptance criterion is a `kill -9`
mid-stream with a bounded, documented loss window; everything unflushed at
that instant is lost, and with unbounded queues that window is whatever the
readers happened to be ahead by. The criterion cannot be met by a design with
no bound to document. Unbounded queues are also the process's memory ceiling
and erase the backpressure signal, which matters once M2's transform competes
for the same disk.

Measured ceilings on the real schema and real payloads (avg 559 B, in-memory
tuple ~1069 B):

| Stage | Measured ceiling |
|---|---|
| `Writer` — `executemany`, real keys, on disk, unique index live | ~37,000 rows/s |
| `Writer` — same, append-only ([ADR-0010](0010-deduplication-in-the-mart.md)) | ~221,000 rows/s |
| `Reader` — parse plus validation, single core | ~174,000 msg/s |
| `Reader` — end-to-end, six workers, unthrottled | ~38,500 msg/s |

The value came from sweeping `maxsize` against durable write rate — rows
committed, not messages read:

| `maxsize` | Durable rate | Queue memory | Loss window |
|---|---|---|---|
| 1,000 | ~19,200 rows/s | ~1 MB | ~50 ms |
| 10,000 | ~21,800 rows/s | ~11 MB | ~460 ms |
| 1,000,000 | ~17,600 rows/s | ~1.1 GB | ~21 s |

The jump from 1,000 buys ~13% by giving the writer larger batches to
amortize commits over. Going further inverts: at 1,000,000 the queue stops
bounding and becomes a buffer, readers run unthrottled at ~38,500 msg/s while
the writer commits ~17,600, and the surplus accumulates. Durable throughput
*falls*, because unthrottled readers doing parse work on the event loop
starve aiosqlite's worker thread. Backpressure was protecting throughput, not
costing it. At 10,000 the loss window is sub-second at the measured rate.

Bounding the DLQ identically matters more than its normal volume suggests:
under a systematic validation failure 100% of traffic routes there, which
would reintroduce the unbounded window on exactly the pathological path this
decision exists to bound.

Accepted costs:

- Ingest throughput is explicitly capped at writer throughput. A writer
  slowdown shows up as a reduced ingest rate rather than growing memory — a
  better failure mode, and visible in the TUI.
- **Quiesce ordering is load-bearing.** A reader suspended inside
  `await queue.put()` only wakes when the writer drains, so stopping the
  writer first deadlocks it. The order — readers, drain, then writer — is in
  [TDD-0002](../tdd/0002-cli-orchestration.md) §6.
- The bound is on count, not bytes. A sustained run of large payloads (max
  observed 49 KB) raises memory proportionally.
- Under [ADR-0010](0010-deduplication-in-the-mart.md) the writer keeps pace
  and the queue sits near empty (~210 of 10,000), so backpressure does not
  engage in normal operation. The bound is a ceiling held in reserve, which
  is what makes it available for M2's contention.
- Revisit trigger: if M2's contention drives writer throughput below the
  aggregate read rate, backpressure engages continuously and throttles
  backfill. Correct behaviour, and the signal to re-evaluate both `maxsize`
  and the writer's batching.
