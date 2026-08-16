# ADR-0019: Readers screen their endpoint's witness lag

## Status

Accepted

## Options considered

- **Remove the endpoint from `Config.endpoints`.** One line, immediate, and a
  hardcoded blocklist that goes stale in both directions — it keeps excluding
  a recovered endpoint and says nothing about the next one to fail.
- **Screen at the auditor.** `GapAuditor._probe()` already connects to every
  endpoint, so it is one place and runs before any shard is claimed. But
  `Ingester.__aenter__` constructs readers before `run()` starts the audit,
  so an exclusion decided there arrives after the readers exist.
- **Each reader screens its own endpoint.** `Reader.get_work()` already opens
  a `cursor=0` probe to establish the retention floor, so comparing that
  message's `time_us` against its decoded `rev` costs no new connection, and
  the check runs in the object that acts on it. The measurement repeats once
  per reader rather than once per endpoint. Chosen.

## Decision

The retention probe returns a health flag alongside the retention floor.
`get_endpoint_health()` compares the first commit message's `time_us` against
the `tid_us` decoded from its `rev`; a lag within ten seconds is healthy. A
reader whose endpoint fails claims no work for the rest of the run.

## Why

`jetstream2.us-east` emits events stamped long after they were committed.
Measured across two full runs on separate days:

| endpoint | rows | p50 `time_us − tid_us` | p99 | share over 1 h |
|---|---|---|---|---|
| jetstream.us-west | 19,975,064 | 0.19 s | 2.87 s | 0.20% |
| jetstream.us-east | 12,876,753 | 0.23 s | 3.24 s | 0.23% |
| **jetstream2.us-east** | **4,291,546** | **34,535 s** | **38,041 s** | **100%** |
| jetstream1.us-east | 4,169,233 | 0.32 s | 25.36 s | 0.26% |
| jetstream2.us-west | 3,412,799 | 0.19 s | 3.10 s | 0.27% |
| jetstream1.us-west | 3,309,227 | 0.20 s | 2.87 s | 0.28% |

Consecutive rows from that endpoint share an identical `time_us`, so it emits
a backlog in batches stamped at emission rather than at witness. Its minimum
lag is 30,221 s, so no row escapes — the behaviour is that endpoint's steady
state, not a transient.

Two things break as a result. `time_us` is the ingest cursor, so a reader
claiming a past range from that endpoint receives events stamped near the
present, its `current_cursor` jumps beyond `end_cursor`, and the range
completes without being read. And the gap audit infers coverage from stored
`time_us` ([TDD-0003](../tdd/0003-ingest-backfill-and-gap-recovery.md) §1),
so those rows assert coverage at times nothing was ingested.

Accepted costs:

- **Screening is per-run and self-healing.** A recovered endpoint rejoins on
  the next start with no list to maintain, and a newly degraded one is caught
  without a code change.
- **The comparison keeps its sign.** 79 rows across 48M carry a `tid_us`
  later than the `time_us` observing them, clustered near −98 s — one PDS
  running fast, not an endpoint fault. An absolute value would let clock skew
  disqualify a healthy endpoint.
- **A screened reader is visible.** It keeps running and simply claims
  nothing, and since `Ingester`'s status line counts readers actively
  reading, it shows as missing from `readers:N/M`.
- **The verdict rests on the probe's first commit message.** The failure it
  screens for affects 100% of rows at four orders of magnitude past the
  threshold, so one sample resolves it.
- Losing an endpoint costs little throughput: the pool already reads near the
  ceiling endpoint capacity sets, and the failing endpoint contributed 8.9%
  of rows, most of them unusable.
