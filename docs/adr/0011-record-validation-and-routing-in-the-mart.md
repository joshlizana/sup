# ADR-0011: Validate and route records with Pydantic in the M2 transform

## Status

Accepted

## Options considered

- **Express the validation in SQL.** Port each model's checks to DuckDB JSON
  predicates and emit a `validation` column alongside the extracted fields.
  1.9x faster where DuckDB holds the read, and it reads
  `$.commit.record.$type` by explicit path, so a record carrying a bare
  `type` key cannot confuse it. The transform reads through SQLite
  ([ADR-0002](0002-raw-ingestion-durability.md)), so the rows are
  materialized before any validation runs and that speed is not on offer.
  But routing becomes a `CASE` or filtered inserts rather than a typed
  branch, per-collection column lists live in SQL text, and `models.py` would
  describe a shape nothing enforces. Verified across twelve injected failure
  cases.
- **Validate with Pydantic in the transform.** Read rows out, validate
  through `BlueskyMessage`, write typed results back. The union yields a
  typed record per row, so column extraction follows from the matched model
  rather than a hand-maintained SQL branch, and `models.py` becomes the
  single declaration of record shape. Rows leave the engine and return, at
  roughly half the throughput. Chosen.
- **Move validation back to ingest.** Rejected on measurement by
  [ADR-0010](0010-deduplication-in-the-mart.md): ingest-time validation was
  part of what held the writer at 17.5k rows/s.

## Decision

**Pydantic, in the M2 transform.** The transform runs as an `aiosqlite`
read, a Python validation and routing stage, and a DuckLake write
([ADR-0002](0002-raw-ingestion-durability.md)).

**Routing keys on `commit.collection`, not the matched model**, because 3.83%
of rows are deletes carrying no record, so the union yields `None` and cannot
name their table.

**The discriminator field is named `record_type`, not `type`.** Pydantic
resolves a discriminated union by field name *or* alias, so a field named
`type` with `alias="$type"` accepts either key, and record bodies are
user-controlled. `record_type` leaves `$type` as the only key that can supply
the tag.

## Why

The mart writes one table per collection, so something has to decide which
table a row belongs to and which columns to extract — routing into typed
per-collection tables is what a discriminated union expresses directly.
Measured against 5,859,179 rows, a full 24-hour window:

| Implementation | Throughput | Wall clock |
|---|---|---|
| Validation expressed in DuckDB SQL | 524,040 rows/s | 11.2 s |
| Pydantic, rows read out and results written back | 271,511 rows/s | 21.6 s |

Both agree on the data: every row validates. SQL's speed is spent on the
wrong axis — at 271,511 rows/s a 24-hour window validates in 21.6 s, so a
periodic batch transform is not limited by this, and buying the difference
back would mean maintaining column lists in SQL while `models.py` describes
the same shapes without enforcing them.

The same corpus gives the shape of the work, and is why routing cannot key on
the matched model:

| Operation | Record present | Rows | Share |
|---|---|---|---|
| `create` | yes | 5,634,026 | 96.16% |
| `delete` | no | 224,364 | 3.83% |
| `update` | yes | 789 | 0.01% |

The `type` naming rule comes from a defect this found: the discriminator
resolved on a field named `type`, letting a record's own bare `type` key
choose its model. 6,536 sample rows carry a bare `type` key — 6,535 duplicate
the correct NSID, and one from a third-party posting library carries a vendor
string, failing the match on an otherwise valid post.

Accepted costs:

- **A zero reject rate is ambiguous evidence.** 5,859,179 of 5,859,179 shows
  the corpus passed; it cannot distinguish a clean corpus from a union that
  fails to discriminate. Crafted input is what demonstrates the union
  discriminates: a `$type` contradicting its collection, a missing required
  field, a collection outside the five.
- **The reject path is an instrument.** Its rate is how a change in upstream
  message shape becomes visible, so the count belongs where an operator sees
  it. A validation failure is a row-level reject — `BlueskyMessage` raises
  before the row reaches any table, excluding rather than misfiling it, and
  rejects land in a mart-side table
  ([ADR-0013](0013-service-owned-pruning.md)).
- This supersedes ADR-0002's one-SQL-script property for the transform.
- **`discriminator="$type"` does not work.** The argument resolves against
  the field name, so pointing it at the alias raises `PydanticUserError:
  Model 'PostRecord' needs a discriminator field for key '$type'` at import
  time. Recorded because it is the obvious first thing to try.
- Field defaults on the discriminator are inert. The union extracts the tag
  from the raw input before defaults apply, so a record missing `$type` fails
  with `union_tag_not_found`.
- `update` operations make `(did, rkey)` recur with a new `rev`, and
  ADR-0010's dedup key includes `rev`, so both versions survive
  ([ADR-0014](0014-mart-grain-and-transform-cadence.md)).
- A SQL pass over the DLQ needs its input guarded: DuckDB does not reliably
  short-circuit `CASE` across a vectorized batch, so a leading `json_valid`
  branch leaves later branches exposed and one malformed payload aborts the
  query. `(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END)` works.
  `events` is unaffected, since its payloads parsed through `orjson` before
  being queued.
