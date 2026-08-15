# ADR-0012: Both stores are rolling 24-hour windows

## Status

Accepted. Supersedes ADR-0002's retention sub-decision. The raw store's prune
trigger is further revised by
[ADR-0013](0013-service-owned-pruning.md).

## Context

`sup` reports what is happening on Bluesky now — the name carries that.
Nothing the TUI or dashboard displays asks a question needing history older
than the firehose itself serves.

ADR-0002 framed the two layers differently: SQLite as a temporary buffer,
DuckLake as "the long-term store". That made unbounded growth the default
for value the product does not offer out of the box.

Jetstream's roll-back window is roughly 24 hours, and
[TDD-0003](../tdd/0003-ingest-backfill-and-gap-recovery.md) §1 already
computes that floor on every start to decide what to backfill.

## Options considered

### Option A: The mart accumulates indefinitely

- The mart grows past the firehose window, holding history the raw store
  cannot.
- Pros: supports trend questions spanning weeks or months.
- Cons: unbounded disk for a locally-run single-operator tool; needs its own
  compaction and schema-evolution story; answers questions the dashboard does
  not ask, since [ADR-0005](0005-analytics-dashboard-framework.md) scopes it
  to a handful of focused views.

### Option B: The mart is a rolling window on the same floor

- Both stores prune against the retention floor the audit already computes.
- Pros: one retention concept across both layers, bounded disk, and a mart
  that matches what the TUI and dashboard display.
- Cons: downtime longer than the window is permanently unrecoverable.

## Decision

**Option B.** Raw rows are pruned after a verified mart commit; mart rows are
pruned past `Config.retention`, hardcoded to the 24 hours the firehose
serves. Anyone forking the tool for their own use edits that one value and
the mart accumulates toward it.

[ADR-0013](0013-service-owned-pruning.md) later fixed *which process* prunes
raw and *how much* it keeps: the transform publishes its committed position
and ingest prunes against it, holding a working buffer rather than a window.
The mart remains the rolling 24 hours this ADR describes.

## Consequences

- ADR-0002's "SQLite is not kept as a permanent archive; DuckLake is the
  long-term store" no longer holds. Neither store is an archive by default.
  DuckLake's selection rests on concurrency, unchanged by this ADR: DuckDB's
  single-file format holds an exclusive cross-process lock, so the TUI and
  dashboard cannot read while transform writes, while DuckLake sustained
  concurrent reads and survived `SIGKILL` (see ADR-0002 Consequences).
  Longevity remains available to anyone who wants it.
- **One setting, two consumers.** `Config.retention` names the window the
  operator cares about. Backfill clamps it to what the endpoints still serve
  — `GapAuditor._update_retention_floor` takes whichever of the two is more
  recent — so asking for 30 days scans back as far as Jetstream allows and no
  further. The purge reads the setting directly, so the mart accumulates and
  keeps the full 30 days. Neither consumer needs its own knob.
- **Purging DuckLake takes three steps.** `DELETE` writes markers and adds a
  snapshot without freeing disk. Measured on 400k rows:

  | Step | Rows | Files | Size | Snapshots |
  |---|---|---|---|---|
  | after inserts | 400,000 | 4 | 3.21 MB | 6 |
  | after `DELETE` of 200k | 200,000 | 4 | 3.21 MB | 7 |
  | after `ducklake_expire_snapshots` | 200,000 | 4 | 3.21 MB | 1 |
  | after `ducklake_cleanup_old_files` | 200,000 | 2 | 1.60 MB | 1 |

  A purge implemented as `DELETE` alone holds row count flat while disk
  grows without bound.
- Downtime longer than the firehose's roll-back window leaves a permanent
  hole whatever the setting, since backfill cannot fetch what Jetstream no
  longer serves. Accepted: the tool reports current activity, and TDD-0003's
  Non-goals already decline to record unrecoverable ranges.
- `Config.retention` stays a `computed_field` hardcoded at 24 hours rather
  than becoming a user-facing setting. `config.py` is short and readable, so
  someone adapting the demo for their own use changes it there. No flag, no
  config file, no validation to maintain.
- The mart keeps its watermark and incremental transform. Purge is a
  separate periodic step rather than part of the transform's read path.
