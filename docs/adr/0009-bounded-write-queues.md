# ADR-0009: Bounded in-memory write queues

## Status

Accepted

## Context

Range workers (`Reader`) hand messages to the single `Writer` through two
in-memory `asyncio.Queue`s: `output_queue` for validated events and
`dlq_queue` for payloads that failed to parse or validate. Those queues are
the only thing standing between "received from Jetstream" and "durable in
SQLite".

M1's acceptance criterion is to `kill -9` the process mid-stream and confirm
"no corruption and a **bounded/documented loss window**". Everything sitting
in an unflushed queue at that instant is lost. With unbounded queues, the
size of that window has no ceiling — it is whatever the readers happened to
be ahead by, which is not a quantity that can be stated in advance. The
acceptance criterion cannot be met by a design that has no bound to
document.

The same unbounded queues are also the process's memory ceiling, and they
remove any backpressure signal: readers race ahead of the writer freely,
which matters once M2 introduces a DuckDB transform competing for the same
disk.

Measured throughput on the real schema and real payloads (avg 559 B,
in-memory tuple ~1069 B) establishes the budget this decision sits inside:

| Stage | Measured ceiling |
|---|---|
| `Writer` — `executemany`, real keys, on disk, unique index live | ~37,000 rows/s |
| `Writer` — same, append-only ([ADR-0010](0010-deduplication-in-the-mart.md)) | ~221,000 rows/s |
| `Reader` — `json.loads` + Pydantic validation, single core | ~174,000 msg/s |
| `Reader` — end-to-end, six workers, unthrottled | ~38,500 msg/s |

An earlier revision of this ADR quoted ~372,000 rows/s for the writer. That
figure was measured on a tmpfs-backed scratch database using sequential
synthetic keys, and overstates disk-backed performance on real data by
roughly an order of magnitude. The table above supersedes it.

Expected aggregate load is roughly 30,000 msg/s (six range workers during
backfill). Under [ADR-0010](0010-deduplication-in-the-mart.md) the writer
sits comfortably above that; with the unique index it did not, which is what
that ADR resolves.

## Options considered

### Option A: Unbounded queues

- `asyncio.Queue()` with no `maxsize`, the default.
- Pros: readers never block; no risk of a queue-full stall.
- Cons: the crash-loss window has no stateable ceiling, so M1's acceptance
  criterion cannot be satisfied as written. Memory growth is unbounded
  whenever the writer lags. No backpressure, so a slow writer is invisible
  until the process is swapping.

### Option B: Bounded queues, `maxsize=1,000` each

- `asyncio.Queue(maxsize=1000)` for both the event queue and the DLQ.
- Pros: the loss window becomes a computable constant. `put()` suspending
  gives backpressure for free, so readers self-throttle to the writer's real
  rate rather than racing ahead. Memory pinned near 1 MB per queue at
  average payload size. Caps the `executemany` batch at 1000 by
  construction.
- Cons: the bound is on message count, not bytes, so a run of unusually
  large payloads (max observed: 49 KB) makes the memory figure correspondingly
  larger. Requires quiesce to stop readers before the writer, or a reader
  blocked in `put()` never wakes.

### Option C: Bounded much larger (e.g. 10,000+)

- Same mechanism, an order of magnitude more slack.
- Pros: absorbs longer stalls (GC pause, slow fsync, M2 disk contention)
  without ever engaging backpressure.
- Cons: widens the loss window proportionally for no benefit the measured
  headroom shows is needed. Memory ~10 MB per queue.

## Decision

**Option C — both queues bounded at `maxsize=10,000`.**

The deciding factor is that the bound is what makes M1's acceptance
criterion expressible at all. At the measured aggregate rate, 10,000
messages is a **sub-second** crash-loss window: a specific, testable number
rather than "however far ahead the readers got".

The value was chosen by measuring the curve rather than reasoning about it.
Sweeping `maxsize` against durable write rate — rows actually committed, not
messages read:

| `maxsize` | Durable rate | Queue memory | Loss window |
|---|---|---|---|
| 1,000 | ~19,200 rows/s | ~1 MB | ~50 ms |
| 10,000 | ~21,800 rows/s | ~11 MB | ~460 ms |
| 1,000,000 | ~17,600 rows/s | ~1.1 GB | ~21 s |

10,000 is the optimum. The jump from 1,000 buys ~13% by giving the writer
larger batches to amortize commits over. Going further inverts: at 1,000,000
the queue stops acting as a bound and becomes a buffer, readers run
unthrottled at ~38,500 msg/s while the writer commits ~17,600, and the
surplus accumulates in memory. Durable throughput *falls*, because
unthrottled readers doing JSON and Pydantic work on the event loop starve
aiosqlite's worker thread. Backpressure was not costing throughput; it was
protecting it by pacing readers to what the writer could absorb.

Bounding the DLQ identically matters more than its normal volume suggests.
Under a systematic validation failure — precisely what a schema mismatch
produces — 100% of traffic routes there, so an unbounded DLQ would
reintroduce the unbounded window on exactly the pathological path this
decision exists to bound.

The `maxsize` also satisfies the count half of M1's "commit every N messages
or X ms, whichever first" batched-writer requirement without a separate
counter: the writer drains everything available on each pass, so a queue
capped at 10,000 caps the batch at 10,000.

## Consequences

- M1's acceptance test now has a number to verify against: at most
  `maxsize` messages per queue, plus whatever is inside the in-flight
  `executemany`, can be lost to a `kill -9`.
- Ingest throughput is now explicitly capped at writer throughput. That is
  the intended relationship, but it means a writer slowdown shows up as
  reduced ingest rate rather than as growing memory — a better failure mode,
  and a visible one via the TUI's ingest-rate metric.
- Under [ADR-0010](0010-deduplication-in-the-mart.md) the writer keeps pace
  in real time and the queue sits near empty (~210 of 10,000), so
  backpressure no longer engages during normal operation. The bound is now
  a ceiling held in reserve rather than an active throttle — which is what
  makes it available to absorb M2's contention when that arrives.
- **Quiesce ordering becomes load-bearing rather than merely tidy.** A
  reader suspended inside `await queue.put()` only wakes when the writer
  drains. Stopping the writer first deadlocks it. The required order —
  readers, then drain, then writer — is recorded in
  [TDD-0002](../tdd/0002-cli-orchestration.md) §6.
- The memory bound is on count, not bytes. A sustained run of large payloads
  raises actual memory use proportionally; nothing enforces a byte ceiling.
- Revisit trigger: if M2's cross-process SQLite/DuckDB contention (the
  accepted open risk in
  [ADR-0002](0002-raw-ingestion-durability.md)) drives writer throughput
  below the aggregate read rate, backpressure will engage continuously and
  visibly throttle backfill. That is correct behavior, but it is the signal
  to re-evaluate both `maxsize` and the writer's batching strategy.
