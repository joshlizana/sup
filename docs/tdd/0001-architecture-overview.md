# TDD-0001: Architecture overview

## Summary

`sup` is four layers, each with a narrow responsibility, connected in a line
from network to human:

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
a minimal version of all four layers working end-to-end.

## Goals / Non-goals

**Goals:**

- Each layer replaceable and testable independently.
- The raw store is the source of truth; the mart is derived and rebuildable
  from it.
- The dashboard is the only store reader. The TUI takes its status over the
  control plane ([ADR-0015](../adr/0015-tui-as-control-plane-client.md)).

**Non-goals:**

- Real-time guarantees between ingest and dashboard — the mart may lag.
- Multi-source ingestion. Jetstream is the only source.

## Design

### Cross-cutting: CLI entry point

A single Typer CLI is the entry point into every layer, sitting outside the
data flow ([ADR-0004](../adr/0004-cli-and-tui-frameworks.md)). Subcommands run
each component independently; **bare `sup` orchestrates all four** as separate
processes it supervises, with the TUI in the main process
([ADR-0006](../adr/0006-cli-orchestration-model.md), broken down in
[TDD-0002](0002-cli-orchestration.md)).

### [1] Ingest client

Connects over websocket, applies server-side collection filtering, and hands
messages to the durable store. On every start it detects gaps between its last
durably-recorded position and Jetstream's retention floor and backfills the
recoverable portion in parallel, sharded onto a priority work queue ordered
oldest-first with multiple range workers feeding one writer. See
[TDD-0003](0003-ingest-backfill-and-gap-recovery.md), which delivers M1's
bounded loss window.

One of two components [ADR-0001](../adr/0001-language-and-dependency-philosophy.md)
calls out as hand-rolled by design.

### [2] Durable raw store

One SQLite database, `WAL`, `synchronous=NORMAL`, written by a batched writer
committing every N messages or X ms. WAL allows the mart transform to read
while ingest writes. Durable across a process crash, not across power loss
([ADR-0002](../adr/0002-raw-ingestion-durability.md)).

The raw store is a working buffer, not a window. The transform publishes its
committed watermark; ingest reads it and prunes below it on its own cadence,
so each store keeps one writer
([ADR-0013](../adr/0013-service-owned-pruning.md)). Steady state is roughly
one transform cycle's backlog. `gap_index` outlives the rows it describes and
is the durable record of what was ingested. The mart is the rolling 24-hour
window ([ADR-0012](../adr/0012-rolling-retention-window.md)); neither store is
an archive.

The table is append-only, keyed solely on its autoincrement PK. Events
arriving twice are stored as they arrive and resolved by [3] — a uniqueness
constraint on event identity costs a random index write on every insert and
degrades as the table grows
([ADR-0010](../adr/0010-deduplication-in-the-mart.md)).

### [3] Mart transform

A DuckDB process that `ATTACH`es the raw store and a separate SQLite file used
as the DuckLake catalog, then writes into DuckLake. The path is a SQL read, a
Pydantic validation and routing stage, and a DuckLake write: records are
validated through `models.py`'s discriminated union and routed to
per-collection tables on `commit.collection`
([ADR-0011](../adr/0011-record-validation-and-routing-in-the-mart.md)).
Routing keys on the collection rather than the matched model because deletes —
3.8% of rows — carry no record.

It reads incrementally via a watermark on the raw store's PK and
**deduplicates on `(did, rkey, rev)`** as it goes. Staying behind the write
frontier is also what makes concurrent reads safe (ADR-0002).

A mart row is one validated event and every revision is kept. Event time comes
from the decoded `rev`, not `time_us`
([ADR-0018](../adr/0018-event-time-from-commit-rev.md)). A cycle starts when
the raw store holds ~15,000 unconsumed rows and takes everything available, so
batches track arrival rate
([ADR-0014](../adr/0014-mart-grain-and-transform-cadence.md)). That interval
is where the ingest-to-dashboard lag lives.

### [4a] Operational TUI

Shows pipeline health — connection status, total ingest rate across all
connections ([TDD-0003](0003-ingest-backfill-and-gap-recovery.md) §6),
ingest/mart lag, error history. An operational view, distinct from the
analytics dashboard. Built with Textual
([ADR-0004](../adr/0004-cli-and-tui-frameworks.md)).

Every number comes over the control plane: each service answers a `status`
command about itself and the TUI holds no database connection
([ADR-0015](../adr/0015-tui-as-control-plane-client.md)). It is a
control-plane client in both directions, also sending pause, resume and
shutdown ([TDD-0002](0002-cli-orchestration.md) §6), with `ctrl+q` running the
ordered shutdown visibly.

### [4b] Analytics dashboard

Reads the mart to answer questions about the *content* ingested, as opposed to
the TUI's health view. Built with Streamlit
([ADR-0005](../adr/0005-analytics-dashboard-framework.md)), scoped to a
handful of focused views, with queries wrapped in `@st.cache_data`.

## Open questions

- Exact watermark state-table shape. It also carries the position ingest
  prunes against ([ADR-0013](../adr/0013-service-owned-pruning.md)), so both
  services read it.
- Per-collection mart table columns — which fields each model contributes.
  Grain, routing and version policy are settled
  ([ADR-0014](../adr/0014-mart-grain-and-transform-cadence.md)).
- Orchestration remains proposed rather than built
  ([TDD-0002](0002-cli-orchestration.md)).
- **Resolved:** cross-process reads work while ingest writes, provided the
  reader stays behind the write frontier
  ([ADR-0002](../adr/0002-raw-ingestion-durability.md)).
- **Resolved:** the 46.19% duplicate share is the live tail replaying against
  endpoints that clamp recent cursors
  ([ADR-0020](../adr/0020-live-tail-cursor-clamping.md)).
- **Resolved:** each service records its own discards. Ingest's DLQ stays in
  the raw store; validation rejects land in a mart-side reject table.
- **Resolved:** the raw-table schema is `src/sup/boostrap.py`.

## Alternatives considered

A single-layer design — the ingest client writing directly into a queryable
store both UIs read — was rejected: it ties the durability format to query
ergonomics, the coupling
[ADR-0002](../adr/0002-raw-ingestion-durability.md) argues against.
