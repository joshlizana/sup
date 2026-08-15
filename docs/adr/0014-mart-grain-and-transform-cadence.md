# ADR-0014: Mart grain is one row per event; cycles trigger on backlog

## Status

Accepted. Resolves TDD-0001's "mart schema/structure" and "every version vs.
latest" open questions.

## Context

[ADR-0011](0011-record-validation-and-routing-in-the-mart.md) settled how
records are validated and routed but not what a mart row represents. The
dashboard is scoped to volume and rhythm for the thin slice — activity per
collection over time — with content analysis (terms, links, languages) as the
full product. The mart has to serve the first without foreclosing the second.

Cadence is constrained by measurement. ADR-0002's benchmark found DuckLake's
cost scales with commit frequency, falling to 460 rows/s at live-tail batch
sizes against SQLite's 110,940. The live stream runs at a measured 250 rows/s
median across the five collections — ~21.6M records per day — so cadence has
to come from batching rather than from a short timer.

## Options considered

### Option A: Rollups only

- The transform aggregates into time buckets and writes only counts.
- Pros: thousands of rows per day, trivial queries, negligible disk.
- Cons: discards the record text, which ends the content-analysis path. The
  mart stops being rebuildable into anything other than the same counts.

### Option B: One row per event

- Per-collection tables holding every validated record, deduped on
  `(did, rkey, rev)`. The dashboard aggregates at read time.
- Pros: content analysis is a query change rather than a re-model; one
  transform stage and one watermark.
- Cons: ~21.6M rows/day, and read-time aggregation for every dashboard view.

### Option C: Events plus derived rollups

- Per-event tables, plus rollup tables the dashboard reads.
- Pros: fastest dashboard queries with the content path intact.
- Cons: two transform stages and two watermarks to keep consistent.

## Decision

**Option B.** Per-collection mart tables hold one row per validated event.
A 24-hour window is ~21.6M rows spread across five tables, which DuckDB
aggregates comfortably for the handful of focused views
[ADR-0005](0005-analytics-dashboard-framework.md) scopes the dashboard to.
Option C's second stage stays available for when a view needs it.

**Every version is kept.** `update` operations are 0.01% of rows (789
measured), and ADR-0010's dedup key already includes `rev`, so both versions
survive without any code. A consumer wanting current state resolves it with a
window function.

**A cycle triggers on backlog, not on a timer.** The transform runs when the
raw store holds at least ~15,000 unconsumed rows — a minute of a slow 250/s
firehose — and each cycle takes everything available rather than a fixed
batch.

## Consequences

- **The threshold is a floor for starting, not a cap on size.** During
  backfill, rows arrive at ~25k/s, so each cycle finds a large backlog and
  commits it in one transaction; at the measured 250 rows/s live tail, the
  threshold is reached about every 60 seconds. Batch size tracks arrival rate
  without a separate adaptive policy.
- **DuckLake maintenance runs on a schedule.** ~1,400 commits per day at the
  live tail is ~1,400 snapshots and their Parquet files. Per
  [ADR-0012](0012-rolling-retention-window.md), `DELETE` alone reclaims
  nothing — `ducklake_expire_snapshots` and `ducklake_cleanup_old_files` have
  to run periodically, not occasionally.
- **The first cycle commits a full window.** The gap audit backfills 24 hours
  at start, so a first-run dashboard shows a populated chart within a cycle or
  two of launch.
- The mart is an append-only log of what the firehose reported, including
  superseded revisions. Views presenting current state are additive later.
- Record text reaches the mart from the first milestone, so the content phase
  changes queries and dashboard views only.
- Read-time aggregation is a revisit trigger: if a dashboard view gets slow
  enough to notice, Option C's rollup tables are the answer, added without
  changing what is already stored.
