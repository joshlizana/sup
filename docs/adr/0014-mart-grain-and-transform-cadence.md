# ADR-0014: Mart grain is one row per event; cycles trigger on backlog

## Status

Accepted

## Options considered

- **Rollups only.** The transform aggregates into time buckets and writes
  counts: thousands of rows per day, trivial queries, negligible disk. It
  discards the record text, which ends the content-analysis path and leaves
  the mart rebuildable into nothing but the same counts.
- **One row per event.** Per-collection tables holding every validated
  record, deduped on `(did, rkey, rev)`, with the dashboard aggregating at
  read time. Content analysis becomes a query change rather than a re-model,
  and there is one transform stage and one watermark. Costs ~27M rows/day
  and read-time aggregation for every view. Chosen.
- **Events plus derived rollups.** Per-event tables plus rollup tables for
  the dashboard: the fastest queries with the content path intact, at two
  transform stages and two watermarks to keep consistent.

## Decision

Per-collection mart tables hold one row per validated event.

**Every version is kept.** `update` operations are 0.01% of rows (789
measured), and [ADR-0010](0010-deduplication-in-the-mart.md)'s dedup key
already includes `rev`, so both versions survive without any code. A consumer
wanting current state resolves it with a window function.

**A cycle triggers on backlog, not on a timer.** The transform runs when the
raw store holds at least ~15,000 unconsumed rows — a minute of a slow 250/s
firehose — and each cycle takes everything available rather than a fixed
batch.

## Why

The dashboard is scoped to volume and rhythm for the thin slice — activity
per collection over time — with content analysis as the full product, so the
mart has to serve the first without foreclosing the second. A 24-hour window
is ~27M rows spread across five tables, which DuckDB aggregates comfortably
for the handful of focused views
[ADR-0005](0005-analytics-dashboard-framework.md) scopes the dashboard to.
The second stage stays available for when a view needs it.

Cadence is constrained by measurement.
[ADR-0002](0002-raw-ingestion-durability.md)'s benchmark found DuckLake's
cost scales with commit frequency, falling to 460 rows/s at live-tail batch
sizes against SQLite's 110,940. The live stream runs at a measured 250 rows/s
median across the five collections, so cadence has to come from batching
rather than from a short timer.

Accepted costs:

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
  at start, so a first-run dashboard shows a populated chart within a cycle
  or two of launch.
- The mart is an append-only log of what the firehose reported, including
  superseded revisions. Views presenting current state are additive later.
- Record text reaches the mart from the first milestone, so the content phase
  changes queries and dashboard views only.
- Revisit trigger: if a dashboard view gets slow enough to notice, rollup
  tables are the answer, added without changing what is already stored.
