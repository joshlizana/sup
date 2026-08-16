# ADR-0020: The live tail is defended against cursor clamping

## Status

Accepted

## Options considered

- **Absorb the duplicates in the mart.** Deduplication already happens there
  ([ADR-0010](0010-deduplication-in-the-mart.md)), so the data is correct
  either way — at the cost of carrying a 46% inflation in read volume,
  storage and transform work for the life of the run.
- **Drop the two v2 endpoints from the pool.** Removes the clamping source
  outright, but they supply half of all backfill rows, and it leaves the
  reader's cursor regression in place — a v1 endpoint triggers the same
  failure in the opposite direction
  ([ADR-0019](0019-endpoint-witness-lag-screening.md)).
- **Defend the reader against clamping.** Correctness stops depending on any
  endpoint honouring its cursor. Chosen.

## Decision

Two changes:

1. **`current_cursor` only advances.** A message with an older `time_us`
   leaves it unchanged, so a clamped stream cannot rewind a reader's position
   or the range it re-queues.
2. **A message outside the claimed range ends the connection** rather than
   being stored. Past `end_cursor` the range is complete and the cursor moves
   there; below `start_cursor` the endpoint has answered from its replay
   floor, and the range returns to the queue for one that can serve it.

**The live tail keeps its ten-second overlap.** A clamping endpoint is
ineligible for the open-ended range, so the tail is only ever held by one
that honours its cursor at any age and nothing clamps there. The overlap goes
on doing its original job of covering the handoff between the trailing
backfill range and the tail.

## Why

The raw store reached 46.19% duplicates, and the distribution localized the
cause. Sampling identities from eight windows across a 48,034,622-row store:

| pk region | mean copies | identities with 5+ copies |
|---|---|---|
| 0 – 18.0M | 1.03 – 1.49 | 0 |
| 24.0M | 4.53 | 6,555 |
| 30.0M | 16.22 | 33,723 |
| 36.0M – 42.0M | 15.14 – 17.39 | all sampled |

Duplication begins at roughly pk 24M — the point where backfill finishes and
the readers move to the live tail — and the repeats come almost entirely from
the two v2 endpoints, which supply 86.1% of rows after that point against
50.7% before it.

**Those endpoints clamp recent cursors to a fixed replay floor.** Sweeping
fourteen cursor ages twice, with a v1 host as control:

| endpoint | floor, round 1 | floor, round 2 | cursors honored |
|---|---|---|---|
| jetstream.us-east | now − 5,243 s | now − 5,298 s | older than ~5,400 s |
| jetstream.us-west | now − 6,959 s | now − 7,013 s | older than ~7,200 s |
| jetstream1.us-east | — | — | all fourteen |

The floor moved by the 55 seconds that elapsed between rounds, so it is a
fixed timestamp rather than a fixed age. A cursor older than it is honored to
the millisecond, a cursor newer is answered from the floor, and `cursor = now`
returns live. This matches v2's sealed-segment design: the shim can seek
within sealed segments, and the recent unsealed window has no seek point.

Backfill therefore runs clean, since its cursors are hours old. The live tail
reconnects with a cursor seconds old and is thrown back 87 to 116 minutes.
`Reader.process_message` then compounds it, assigning
`self.current_cursor = time_us` unconditionally so a clamped message drives
the cursor **backward**. `run()` re-queues that regressed position, the next
claim replays from there, and each cycle re-reads the window — which is how
identities reach 15 to 22 copies rather than the 2 an overlap produces.

With these changes a run measures 0.56% duplicates over 27,048,844 rows,
flat across every pk region and 0.00% in the region the live tail writes,
against 0.556% predicted from shard overlap alone
([ADR-0010](0010-deduplication-in-the-mart.md)).

Accepted costs:

- **Rows written and distinct events are now the same figure**, so a rate is
  readable as either. The live tail settles at 230-250 events/sec and
  backfill reads 32,000-38,000/s of real history.
- v2 endpoints stay in the pool, carrying 59.3% of rows from two hosts
  against 40.7% from three v1 hosts on the run above — roughly 2x per host,
  matching their backfill share.
- **v2's native replay path is not an option.** Reading sealed segments the
  way the instances are designed for, rather than through the websocket
  cursor shim, requires an API key. A keyed dependency contradicts
  [ADR-0003](0003-packaging-and-distribution-via-uv.md), where `uvx sup` runs
  with nothing to obtain first.
- The floor is a property of each instance's sealing schedule, so it differs
  per endpoint and moves. Nothing in the design should hardcode it; changes 1
  and 2 hold whatever it is.
- A reader that claims a range starting inside a clamped window ends the
  connection on the first message, so the range goes back to the queue and is
  attempted again — by that endpoint or another — until one that honours the
  cursor takes it. The cost is a reconnect per attempt rather than reading
  through the replay, and it resolves faster the more honouring endpoints the
  pool holds.
