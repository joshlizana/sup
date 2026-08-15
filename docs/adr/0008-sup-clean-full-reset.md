# ADR-0008: `sup clean` — full data reset

## Status

Accepted

## Context

`sup clean` is a full reset/wipe of all ingested data (the raw SQLite store,
the DuckLake catalog and data files) — a destructive dev/testing utility,
not a routine maintenance operation. It needs to coordinate with `sup
ingest`/`sup transform` if they're currently running (both must be paused
before wiping, so neither is mid-write when the files disappear out from
under it), but should also work if neither is running at all, since the
control-plane lock being free already proves nothing holds the files.

This builds directly on the pause/shutdown machinery from
[TDD-0002](../tdd/0002-cli-orchestration.md) §6 — both `sup ingest` and
`sup transform` now support pause/resume symmetrically (resolving that
TDD's open question), which is what makes `sup clean` possible without new
IPC mechanism.

## Decision

- **If both locks are free** (neither service running): wipe directly —
  delete/reset the raw SQLite file and DuckLake catalog/data files. No
  pause/resume needed, since nothing is using them.
- **If either service is running** (lock held): `sup clean` connects to
  both `sup ingest`'s and `sup transform`'s control-plane Listeners as a
  `ServiceClient`, sends pause to both (no ordering requirement between the
  two — unlike shutdown's ordering, pausing is symmetric, so both can be
  requested concurrently and awaited together), waits for both to confirm
  quiesced, performs the wipe, then sends resume to both.
- **Destructive — requires explicit confirmation** before proceeding. Not
  something that runs on a bare invocation: an interactive confirmation
  prompt, or an explicit opt-in flag (e.g. `--yes`/`--force`) for
  non-interactive/scripted use. Exact flag naming is an implementation
  detail, not blocking this decision.
- **Resume-after-clean is not the same as resume-after-pause.** Ordinary
  pause→resume wants cursor continuity (no gap in ingested data). Resume
  after a full wipe should start fresh with no cursor — there is nothing
  left for the old cursor to "continue from." This falls out naturally as
  long as cursor/resume state lives inside the raw SQLite store that `sup
  clean` wipes (the common case); if cursor state is ever stored anywhere
  else, `sup clean` needs to explicitly clear that too. Verify this holds
  once M1 defines exactly where cursor state lives.

## Consequences

- No new IPC mechanism needed — `sup clean` reuses the same self-connect
  `ServiceClient` pattern already established for pause/shutdown delivery
  (TDD-0002 §6), just directed at two services instead of one.
- The confirmation requirement means `sup clean` cannot be used unattended
  without an explicit opt-in flag — worth deciding the exact UX (prompt
  wording, flag name) when it's actually built, not now.
- Depends on a fact not yet settled: exactly where ingest's cursor/resume
  state lives. If it's part of the raw SQLite store, this ADR's assumption
  holds with no extra work; if not, `sup clean` gains a second thing it
  must explicitly clear.
