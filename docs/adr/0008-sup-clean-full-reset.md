# ADR-0008: `sup clean` — full data reset

## Status

Accepted

## Decision

`sup clean` wipes all ingested data — the raw SQLite store, `index.db`, the
DuckLake catalog, and the DuckLake data files. The raw stores sit under
`Config.data_path` and the mart under `Config.ducklake_path`.

- **Both locks free**, meaning neither service is running: wipe directly.
  Nothing is using the files, so no pause or resume is involved.
- **Either lock held:** connect to both `sup ingest`'s and `sup transform`'s
  control-plane Listeners as a `ServiceClient`, send pause to both, wait for
  both to confirm quiesced, wipe, then send resume to both. Pausing is
  symmetric, so unlike shutdown there is no ordering requirement — both can
  be requested concurrently and awaited together.
- **Destructive, so it requires explicit confirmation**: an interactive
  prompt, or an opt-in flag for scripted use. Exact flag naming is an
  implementation detail.
- **Resume after clean is not resume after pause.** Ordinary pause and resume
  wants cursor continuity so no gap appears in ingested data; resume after a
  full wipe starts fresh with no cursor, because nothing is left for the old
  one to continue from.

## Why

This is a destructive dev and testing utility, not routine maintenance. It
has to coordinate with running services so neither is mid-write when the
files disappear, and it has to work with nothing running, since a free
control-plane lock already proves nothing holds the files.

No new IPC is needed: both services support pause and resume symmetrically
([TDD-0002](../tdd/0002-cli-orchestration.md) §6), so `sup clean` reuses the
same self-connect `ServiceClient` pattern already used for pause and shutdown
delivery, directed at two services instead of one.

Accepted costs:

- The confirmation requirement means `sup clean` cannot run unattended
  without an explicit opt-in flag.
- Ingest's position is derived by scanning `gap_index`, which outlives the
  rows it describes ([ADR-0013](0013-service-owned-pruning.md)), so `index.db`
  is part of the wipe. A clean that spares it leaves `gap_index` asserting
  coverage for events that are gone, and the next start's audit finds no gap
  to backfill.
