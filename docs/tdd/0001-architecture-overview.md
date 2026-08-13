# TDD-0001: Architecture overview

## Summary

`sup` is organized as four layers, each with a narrow, well-defined
responsibility, connected in a straight line from network to human:

```
Jetstream (network)
      |
      v
[1] Ingest client  --------->  [2] Durable raw store
                                        |
                                        v
                                [3] Mart transform  --------->  Data mart
                                                                    |
                                                    +---------------+---------------+
                                                    v                               v
                                          [4a] Operational TUI            [4b] Analytics dashboard
```

The first milestone (see [TODO.md](../TODO.md)) is a **thin vertical slice**:
a minimal version of all four layers working end-to-end, before any one layer
is deepened.

## Goals / Non-goals

**Goals:**
- Each layer replaceable/testable independently (e.g. swap the durability
  format without touching the ingest client or the TUI).
- The raw store is the system's source of truth; the mart is derived and
  rebuildable from it.
- The analytics dashboard reads only the mart, never the raw store, keeping
  the raw store's format free to be optimized purely for durable,
  high-throughput writes rather than query ergonomics. The operational TUI is
  the one exception — see [4a] below, it needs the raw store directly for
  metrics the mart alone can't provide.

**Non-goals:**
- Real-time guarantees between ingest and dashboard — the mart is allowed to
  lag behind the raw store (see [TODO.md](../TODO.md) milestones for how much
  lag is acceptable at each stage).
- Multi-source ingestion — this design assumes Jetstream is the only source.

## Design

### Cross-cutting: CLI entry point

A single Typer-based CLI (`sup ingest`, `sup transform`, `sup tui`,
`sup dashboard`, etc.) is the entry point into every layer below — not a
layer itself, since it doesn't sit in the ingest→raw→mart data flow. See
[ADR-0004](../adr/0004-cli-and-tui-frameworks.md) for the framework choice.

### [1] Ingest client

Connects to Jetstream over websocket, applies any server-side filtering
(collection/DID) Jetstream supports, and hands received messages to the
durable store. Owns reconnect/backoff logic and — per
[ADR-0002](../adr/0002-raw-ingestion-durability.md) — should use Jetstream's
cursor-resume support to minimize the gap around a reconnect, on top of (not
instead of) the durable store's own crash recovery.

This is one of the two components explicitly called out in
[ADR-0001](../adr/0001-language-and-dependency-philosophy.md) as hand-rolled
by design — it's the project's core, not a solved problem being reinvented.

### [2] Durable raw store

A single SQLite database, journal mode `WAL`, `synchronous=NORMAL`. The
ingest client writes to it through a batched writer that commits every N
messages or every X ms, whichever comes first — bounding both memory and
write latency regardless of traffic pattern. WAL mode allows the TUI and the
mart transform to read concurrently while the ingest client keeps writing.
Durable across a process crash; not guaranteed across power loss/OS crash —
see [ADR-0002](../adr/0002-raw-ingestion-durability.md) for the exact
tradeoff. Rows are pruned only after the mart transform has verified the
corresponding DuckLake write committed — SQLite is not a permanent archive.

### [3] Mart transform

A DuckDB process that `ATTACH`es the raw store's SQLite file and a separate
SQLite file used as the DuckLake catalog (kept separate from the raw store to
avoid mixing high-frequency ingest WAL churn with catalog metadata I/O), and
writes the transformed result into DuckLake — ideally as one SQL script
covering the whole attach → transform → write path. Reads incrementally via a
monotonic watermark column plus a small state table, rather than rescanning
or reprocessing the whole raw store on every run. Runs periodically or
on-demand rather than per-message — this is where the "lag between ingest and
dashboard" tradeoff from the Non-goals section actually lives. Mart schema
itself is still TBD (future TDD). See
[ADR-0002](../adr/0002-raw-ingestion-durability.md) for why this design was
chosen and for an accepted open risk: cross-process SQLite/DuckDB
concurrency (ingest process writing WAL while a separate DuckDB process reads
via `ATTACH`) hasn't been validated ahead of time and is expected to be
proven out (or not) as this milestone is actually built.

### [4a] Operational TUI

Shows the health/state of the ingest pipeline itself — connection status,
ingest rate, lag between raw store and mart, error/reconnect history. Reads
the raw store directly for ingest-side metrics (row count, last write time)
in addition to the mart, since computing raw-store/mart lag requires both.
This is an *operational* view (is the pipeline healthy?), distinct from the
analytics dashboard, and is the one component that doesn't follow the
mart-only read rule in Goals above. Built with Textual — see
[ADR-0004](../adr/0004-cli-and-tui-frameworks.md) for the framework choice
and its accepted governance-risk tradeoff.

### [4b] Analytics dashboard

Reads the mart to answer questions about the *content* of what's been
ingested, not the pipeline's health. Separate concern from the TUI even
though both are UI layers over the same underlying mart. Built with
Streamlit — see [ADR-0005](../adr/0005-analytics-dashboard-framework.md) for
the framework choice; scope is deliberately a handful of focused views, not
a large multi-page app, which is part of why that choice was made. Mart
queries backing dashboard views should use `@st.cache_data`.

## Open questions

- Exact SQLite raw-table schema — beyond the raw JSON message body, what
  columns are extracted for indexing/watermarking (e.g. kind, collection,
  DID, `time_us`)?
- Exact watermark state-table shape (single row vs. per-source, what it
  tracks precisely).
- Cross-process SQLite/DuckDB concurrency under real load — accepted open
  risk per [ADR-0002](../adr/0002-raw-ingestion-durability.md), not yet
  proven out; expected to be resolved (or force a design revisit) during M1/M2.
- Mart schema/structure — not yet designed; depends on what questions the
  analytics dashboard needs to answer (TBD, likely a future TDD).

## Alternatives considered

An alternative single-layer design (ingest client writes directly into a
queryable store that both TUI and dashboard read from, no separate raw/mart
split) was considered implicitly and rejected: it would tie the durability
format's design to query ergonomics, which is exactly the coupling
[ADR-0002](../adr/0002-raw-ingestion-durability.md) argues against (see
Option B's discussion of blurring the raw-store/mart boundary).
