# ADR-0011: Validate and route records with Pydantic in the M2 transform

## Status

Accepted

## Context

[ADR-0010](0010-deduplication-in-the-mart.md) removed Pydantic validation
from the ingest path and assigned it to the M2 transform, but left the
implementation open. `models.py` already describes the Jetstream message
shape — a `BlueskyMessage` with a `Commit`, whose `record` is a
discriminated union with one model per collection in the client's
`wantedCollections` filter.

Two things forced the question. First, the mart writes one table per
collection, so something has to decide which table a row belongs to and
which columns to extract for it. Second, ADR-0002 accepted a mart transform
"expressible as one SQL script"; a Python validation stage in the middle
changes that property, so the choice is worth recording rather than
absorbing silently.

Measured against the raw store's 5,859,179 rows — a full 24-hour window of
real traffic — for both candidate implementations:

| Implementation | Throughput | Wall clock |
|---|---|---|
| Validation expressed in DuckDB SQL | 524,040 rows/s | 11.2 s |
| Pydantic, rows read out and results written back | 271,511 rows/s | 21.6 s |

Both agree on the data: every one of the 5,859,179 rows validates. What the
same corpus also established is the shape of the work each has to do:

| Operation | Record present | Rows | Share |
|---|---|---|---|
| `create` | yes | 5,634,026 | 96.16% |
| `delete` | no | 224,364 | 3.83% |
| `update` | yes | 789 | 0.01% |

## Options considered

### Option A: Express the validation in SQL

- Port each model's checks to DuckDB JSON predicates — `json_valid`,
  `payload ->> '$.commit.rkey' IS NULL`, `TRY_CAST(... AS BIGINT)`,
  collection membership via `IN`, `json_type` for the `subject` shape — and
  emit a `validation` column alongside the extracted fields.
- **Pros:** 1.9x faster, and the data never leaves the engine, so the
  transform stays a single `CREATE TABLE ... AS SELECT` and ADR-0002's
  one-SQL-script property survives intact. Reads `$.commit.record.$type`
  by explicit path, so it cannot be confused by a record that also carries
  a bare `type` key.
- **Cons:** routing to per-collection tables becomes a `CASE` or a set of
  filtered inserts rather than a typed branch, and the per-collection column
  lists live in SQL text rather than in declared models. Verified working
  across twelve injected failure cases, but the checks are duplicated
  knowledge — `models.py` would describe a shape nothing enforces.

### Option B: Validate with Pydantic in the transform

- Read raw rows out of the store, validate each through `BlueskyMessage`,
  and write typed results back into the mart tables.
- **Pros:** the discriminated union yields a typed record model per row, so
  per-collection column extraction follows from the model that matched
  rather than from a hand-maintained SQL branch. `models.py` becomes the
  single declaration of record shape, used rather than merely documenting.
- **Cons:** rows leave the engine and come back, which is the cost ADR-0002's
  SQL-only framing was avoiding. Roughly half the throughput of Option A.

### Option C: Move validation back to ingest

- Rejected by [ADR-0010](0010-deduplication-in-the-mart.md) on measurement:
  ingest-time validation was part of what held the writer at 17.5k rows/s.
  Not reopened here.

## Decision

**Option B — Pydantic, in the M2 transform.**

The deciding factor is that the mart's job is routing into typed
per-collection tables, and a discriminated union is a direct expression of
exactly that. Option A is faster, but its speed advantage is spent on the
wrong axis: at 271,511 rows/s a full 24-hour window validates in 21.6
seconds, so a periodic batch transform is nowhere near limited by this. The
round trip is real but affordable, and buying it back would mean maintaining
the per-collection column lists in SQL while `models.py` describes the same
shapes without enforcing them.

**Routing keys on `commit.collection`, not on the matched record model.**
3.83% of rows are deletes carrying no record at all, so the union yields
`None` for them and cannot name their table. `commit.collection` is present
on every row regardless of operation.

## Consequences

- The transform runs as a DuckDB read, a Python validation and routing
  stage, and a DuckLake write, superseding ADR-0002's one-SQL-script
  property (amended there to match). Legibility now rests on the models
  being declarative rather than on the transform being a single query.
- **The discriminator field must not be named `type`.** Pydantic resolves a
  discriminated union by field name *or* alias, so a field named `type` with
  `alias="$type"` accepts either key from the payload — and record bodies
  are user-controlled. 6,536 sample rows carry their own bare `type` key:
  6,535 duplicate the correct NSID, and one from a third-party posting
  library carries a vendor string, failing the union match on an otherwise
  valid post. Naming the field `record_type` leaves `$type` as the only key
  that can supply the tag. Verified at 5,859,179 of 5,859,179 rows.
- **`discriminator="$type"` does not work.** The argument resolves against
  the field name, so pointing it at the alias raises `PydanticUserError:
  Model 'PostRecord' needs a discriminator field for key '$type'` at import
  time. Recorded because it is the obvious first thing to try.
- Field defaults on the discriminator are inert. The union extracts the tag
  from the raw input before defaults apply, so a record missing `$type`
  fails with `union_tag_not_found`.
- A validation failure is a row-level reject: `BlueskyMessage` raises before
  the row reaches any table, excluding it rather than misfiling it. Where
  those rejects land is an M2 decision, alongside TDD-0003's question about
  ingest-side drops.
- `update` operations make `(did, rkey)` recur with a new `rev`. ADR-0010's
  dedup key includes `rev`, so both versions survive; whether the
  per-collection tables expose every version or only the latest is left to
  the mart schema.
- A SQL validation pass over the DLQ would need its input guarded, since
  DuckDB does not reliably short-circuit `CASE` across a vectorized batch: a
  leading `json_valid` branch leaves later branches exposed, and one
  malformed payload aborts the query with `InvalidInputException`.
  `(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END) ->> '$.did'`
  works. `events` is unaffected — its payloads all parsed through `orjson`
  before being queued.
- Revisit trigger: if the transform becomes the bottleneck, Option A's 1.9x
  is on the table, and the twelve-case SQL port that measured it is a
  known-good starting point.
