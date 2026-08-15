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
  the raw store's format optimized purely for durable, high-throughput
  writes. It is the only store reader: the TUI takes its status over the
  control plane instead
  ([ADR-0015](../adr/0015-tui-as-control-plane-client.md)).

**Non-goals:**
- Real-time guarantees between ingest and dashboard — the mart is allowed to
  lag behind the raw store (see [TODO.md](../TODO.md) milestones for how much
  lag is acceptable at each stage).
- Multi-source ingestion — this design assumes Jetstream is the only source.

## Design

### Cross-cutting: CLI entry point

A single Typer-based CLI is the entry point into every layer below, sitting
outside the ingest→raw→mart data flow itself. See
[ADR-0004](../adr/0004-cli-and-tui-frameworks.md) for the framework choice.

Subcommands (`sup ingest`, `sup transform`, `sup tui`, `sup dashboard`) run
each component independently. **Bare `sup` (no subcommand) orchestrates all
four together**, as separate OS processes it supervises (ingest,
mart-transform loop, and the dashboard as subprocesses; the TUI attached to
the main process's terminal) — see
[ADR-0006](../adr/0006-cli-orchestration-model.md) for why this runs as
separate processes, and how it preserves ADR-0002's accepted concurrency
risk boundary. The orchestration work itself is broken down in
[TDD-0002](0002-cli-orchestration.md).

### [1] Ingest client

Connects to Jetstream over websocket, applies any server-side filtering
(collection/DID) Jetstream supports, and hands received messages to the
durable store. On every start, proactively detects whether there's a gap
between its last durably-recorded position and Jetstream's retention floor,
and backfills any recoverable portion in parallel — the recoverable range is
sharded into time ranges on a priority work queue ordered oldest-first, with
multiple range workers feeding the one writer per
[ADR-0002](../adr/0002-raw-ingestion-durability.md). See
[TDD-0003](0003-ingest-backfill-and-gap-recovery.md) for the full design,
which is what delivers M1's "bounded/documented loss window" acceptance
criterion.

This is one of the two components explicitly called out in
[ADR-0001](../adr/0001-language-and-dependency-philosophy.md) as hand-rolled
by design, since it's the project's core.

### [2] Durable raw store

A single SQLite database, journal mode `WAL`, `synchronous=NORMAL`. The
ingest client writes to it through a batched writer that commits every N
messages or every X ms, whichever comes first — bounding both memory and
write latency regardless of traffic pattern. WAL mode allows the TUI and the
mart transform to read concurrently while the ingest client keeps writing.
Durable across a process crash; not guaranteed across power loss/OS crash —
see [ADR-0002](../adr/0002-raw-ingestion-durability.md) for the exact
tradeoff.

The raw store is a working buffer, not a window. The transform publishes its
committed watermark; ingest reads it and prunes below it on its own cadence,
so each store keeps exactly one writer
([ADR-0013](../adr/0013-service-owned-pruning.md)). Steady state is roughly
one transform cycle's backlog. `gap_index` — the audit's incremental
`(pk, time_us)` record in its own `index.db` — outlives the rows it describes
and becomes the durable record of what was ingested, pruned on the retention
floor itself. The mart is the rolling 24-hour window
([ADR-0012](../adr/0012-rolling-retention-window.md)); neither store is an
archive.

The table is append-only, keyed solely on its autoincrement primary key.
Events arriving twice — from the backfill shards' overlap buffers, or from
a restart re-reading a range — are stored as they arrive and resolved by
[3]. See [ADR-0010](../adr/0010-deduplication-in-the-mart.md) for the
measurements behind that: a uniqueness constraint on the event identity
costs a random index write on every insert, which dominated ingest and
degraded as the table grew.

### [3] Mart transform

A DuckDB process that `ATTACH`es the raw store's SQLite file and a separate
SQLite file used as the DuckLake catalog (kept separate from the raw store to
avoid mixing high-frequency ingest WAL churn with catalog metadata I/O), and
writes the transformed result into DuckLake. The path is a SQL read, a
Python validation and routing stage, and a DuckLake write: records are
validated through `models.py`'s discriminated union and routed to
per-collection mart tables on `commit.collection`
([ADR-0011](../adr/0011-record-validation-and-routing-in-the-mart.md)).
Routing keys on the collection rather than on the matched record model
because deletes — 3.8% of rows — carry no record to match. Reads
incrementally via a monotonic watermark column plus a small state table,
and **deduplicates on
`(did, rkey, rev)`** as it goes, since the raw store keeps every copy it
received ([ADR-0010](../adr/0010-deduplication-in-the-mart.md)). Measured
duplicate share in the raw store is roughly 2%.

A mart row is one validated event, and every revision is kept. A cycle starts
when the raw store holds ~15,000 unconsumed rows and takes everything
available, so batches track arrival rate — large during backfill, about every
65 seconds at the live tail
([ADR-0014](../adr/0014-mart-grain-and-transform-cadence.md)). That interval
is where the "lag between ingest and dashboard" tradeoff from the Non-goals
section lives. See
[ADR-0002](../adr/0002-raw-ingestion-durability.md) for why this design was
chosen and for an accepted open risk: cross-process SQLite/DuckDB
concurrency (ingest process writing WAL while a separate DuckDB process reads
via `ATTACH`) hasn't been validated ahead of time and is expected to be
proven out (or not) as this milestone is actually built.

### [4a] Operational TUI

Shows the health/state of the ingest pipeline itself — connection status,
ingest rate (total across *all* active connections — backfill shards plus
the live tail, per [TDD-0003](0003-ingest-backfill-and-gap-recovery.md) §6,
so the TUI stays representative while backfill dominates throughput), lag
between ingest and the mart, error/reconnect history. This is an
*operational* view (is the pipeline healthy?), distinct from the analytics
dashboard. Built with Textual — see
[ADR-0004](../adr/0004-cli-and-tui-frameworks.md) for the framework choice
and its accepted governance-risk tradeoff.

Every number comes over the control plane. Ingest and transform each answer a
`status` command about themselves, and the TUI holds no database connection
([ADR-0015](../adr/0015-tui-as-control-plane-client.md)) — the readers live in
another process, and the raw store's row count measures backlog rather than
volume. The TUI is a control-plane client in both directions: it also sends
pause, resume, and shutdown (per
[ADR-0007](../adr/0007-control-plane-ipc.md) and
[TDD-0002](0002-cli-orchestration.md) §6), and `ctrl+q` runs the ordered
shutdown with each service's state visible as it goes.

### [4b] Analytics dashboard

Reads the mart to answer questions about the *content* of what's been
ingested — a separate concern from the TUI's operational-health view, even
though both are UI layers over the same underlying mart. Built with
Streamlit — see [ADR-0005](../adr/0005-analytics-dashboard-framework.md) for
the framework choice; scope is deliberately a handful of focused views,
part of why that choice was made. Mart queries backing dashboard views
should use `@st.cache_data`.

## Open questions

- Exact watermark state-table shape (single row vs. per-source, what it
  tracks precisely). It now also carries the position ingest prunes against
  ([ADR-0013](../adr/0013-service-owned-pruning.md)), so it is read by both
  services.
- Cross-process SQLite/DuckDB concurrency under real load — accepted open
  risk per [ADR-0002](../adr/0002-raw-ingestion-durability.md), narrowed by
  [ADR-0015](../adr/0015-tui-as-control-plane-client.md) to
  transform-writes/dashboard-reads, and expected to resolve (or force a design
  revisit) during M2.
- Per-collection mart table columns — which fields each collection's model
  contributes. The grain, routing, and version policy are settled
  ([ADR-0014](../adr/0014-mart-grain-and-transform-cadence.md)).
- **Resolved:** each service records its own discards. Ingest's DLQ stays in
  the raw store; M2 validation rejects land in a mart-side reject table,
  keeping every store to a single writer per
  [ADR-0013](../adr/0013-service-owned-pruning.md).
- **Resolved:** the raw-table schema is `src/sup/boostrap.py`, and mart grain,
  cadence, and version policy are ADR-0014.
- Orchestration design (subprocess output handling, shutdown/signal
  propagation order, startup readiness) — proposed in
  [TDD-0002](0002-cli-orchestration.md), not yet built or confirmed.

## Alternatives considered

An alternative single-layer design (ingest client writes directly into a
queryable store that both TUI and dashboard read from, no separate raw/mart
split) was considered implicitly and rejected: it would tie the durability
format's design to query ergonomics, which is exactly the coupling
[ADR-0002](../adr/0002-raw-ingestion-durability.md) argues against (see
Option B's discussion of blurring the raw-store/mart boundary).
