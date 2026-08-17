# TDD-0001: Architecture overview

## Summary

`sup` is four layers, each with a narrow responsibility, connected in a line
from network to human:

```
Jetstream (network)
      |
      v
[1] Ingest client  --------->  [2] Durable raw store
      ^                                 |
      |                                 v
      |                         [3] Mart transform  --------->  Data mart
      |                                 |                           |
      |  committed position             |                           v
      +------ watermark.db <------------+               [4b] Analytics dashboard

[4a] Operational TUI  ---- control plane ---->  [1] ingest, [3] transform
```

The first milestone (see [TODO.md](../TODO.md)) is a **thin vertical slice**:
a minimal version of all four layers working end-to-end.

## Goals / Non-goals

**Goals:**

- Each layer replaceable and testable independently.
- The raw store is the durability boundary: an event is safe once it lands
  there, and the mart derives from it. Ingest prunes it to roughly one
  transform cycle's backlog
  ([ADR-0013](../adr/0013-service-owned-pruning.md)), so the mart is the
  queryable record and `gap_index` the coverage record.
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

A process that reads the raw store through `aiosqlite` and writes into
DuckLake, whose catalog is a separate SQLite file. The path is a SQLite read,
a Pydantic validation and routing stage, and a DuckLake write: records are
validated through `models.py`'s discriminated union and routed to
per-collection tables on `commit.collection`
([ADR-0011](../adr/0011-record-validation-and-routing-in-the-mart.md)).
Routing keys on the collection rather than the matched model because deletes —
3.8% of rows — carry no record. The models close the types routing reads and
leave the rest open, so an unfamiliar embed or facet type validates and
lands in its column
([ADR-0024](../adr/0024-strict-at-the-boundary-open-at-the-leaves.md)). The
columns each table lands in are in [TDD-0004](0004-mart-schema.md).

It reads incrementally via a watermark on the raw store's PK, in
100,000-row chunks flushed one at a time
([ADR-0027](../adr/0027-transform-cycle-shape.md)), and **deduplicates on
`(did, rkey, rev)`** as it goes, enforced on the insert through a
two-column hash key
([ADR-0026](../adr/0026-uniqueness-on-insert-with-a-two-column-hash-key.md)). Reading through SQLite
rather than DuckDB is what makes those reads reliable while ingest writes
(ADR-0002).

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

- **Resolved:** the committed position is a single row in `watermark.db`,
  holding the raw-store `pk` the transform has written through
  ([ADR-0023](../adr/0023-committed-position-in-sqlite.md)). The transform
  writes it, ingest reads it through `aiosqlite` and prunes against it
  ([ADR-0013](../adr/0013-service-owned-pruning.md)).
- **Resolved:** per-collection mart table columns are in
  [TDD-0004](0004-mart-schema.md), measured against a 28,996,394-event raw
  store. Grain, routing and version policy are settled
  ([ADR-0014](../adr/0014-mart-grain-and-transform-cadence.md)).
- Orchestration remains proposed rather than built
  ([TDD-0002](0002-cli-orchestration.md)).
- **Resolved:** cross-process reads while ingest writes succeed through
  SQLite's own connection and fail intermittently through DuckDB's
  `sqlite_scanner` at any distance behind the writer, so the transform reads
  through `aiosqlite` and DuckLake takes only the write
  ([ADR-0002](../adr/0002-raw-ingestion-durability.md)).
- **Resolved:** the 46.19% duplicate share was the live tail replaying against
  endpoints that clamp recent cursors. Defended in
  [ADR-0020](../adr/0020-live-tail-cursor-clamping.md) and measured at 0.56%,
  which is the shard overlap and nothing more.
- **Resolved:** each service records its own discards. Ingest's DLQ stays in
  the raw store; validation rejects land in a mart-side reject table.
- **Resolved:** `src/sup/boostrap.py` holds one bootstrap function per
  service, each called from that service's invocation
  ([ADR-0022](../adr/0022-per-service-bootstrap.md)).

## Alternatives considered

A single-layer design — the ingest client writing directly into a queryable
store both UIs read — was rejected: it ties the durability format to query
ergonomics, the coupling
[ADR-0002](../adr/0002-raw-ingestion-durability.md) argues against.
