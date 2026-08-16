# ADR-0002: Durable storage for raw Jetstream ingestion

## Status

Accepted

## Options considered

- **Append-only JSONL segments.** The cheapest write path, with a loss window
  bounded by the fsync cadence, but a truncated trailing line needs repair on
  read and the whole thing is hand-rolled where
  [ADR-0001](0001-language-and-dependency-philosophy.md) prefers a library.
- **Streaming Parquet writer.** A good mart format and a weak raw store: the
  buffer since the last row-group flush is unprotected, so it needs a durable
  log underneath it regardless.
- **DuckDB single-file.** One format for both raw store and mart, but its
  exclusive lock shuts out the TUI and the transform for as long as ingest
  holds it.
- **SQLite as a durable append buffer, with the mart built separately.** Lets
  the write path optimize for crash safety and the mart for queries. Chosen.

## Decision

The raw store is one SQLite database in WAL mode at `synchronous=NORMAL`,
written by a batched writer that commits every N messages or X ms, whichever
comes first. The mart is DuckLake, its catalog a SQLite database in a
separate file. The transform reads the raw store through `aiosqlite`,
`WHERE pk > watermark` against a monotonic watermark column and a small state
table, and writes out through DuckLake. DuckDB never attaches the raw store.

Retention is [ADR-0012](0012-rolling-retention-window.md) and
[ADR-0013](0013-service-owned-pruning.md). Cross-process concurrency is
validated as M1 and M2 land rather than by a throwaway spike.

## Why

The raw store is the source of truth, so crash safety outranks speed, and
killing the process mid-stream is the first thing a reviewer running it
locally will try. Measured on 2M real rows at the writer's 10,000-row batch
size:

| Backend | rows/s | Concurrent read | Survives `SIGKILL` |
|---|---|---|---|
| Arrow IPC | 870,556 | yes | yes |
| Parquet | 557,298 | no | no — lost all 17.16M rows |
| SQLite | 268,607 | yes | yes |
| DuckLake | 248,346 | yes | yes |
| DuckDB single-file | 101,790 | no — exclusive lock | yes |

A Parquet file is unreadable until its footer lands, and DuckDB's single-file
format locks out the TUI and mart. SQLite and DuckLake both survive and both
allow concurrent readers; their costs diverge on commit frequency, since at
the 10-row batches the live tail produces DuckLake reaches 460 rows/s against
SQLite's 110,940. That is the split — SQLite for small frequent commits,
DuckLake for large periodic ones.

`synchronous=NORMAL` fsyncs at checkpoint rather than at every commit, so
commits survive a process crash while a small window stays exposed to power
loss or an OS crash — a documented SQLite tradeoff
([forum](https://sqlite.org/forum/info/9d6f13e346231916)). That satisfies
M1's `kill -9` criterion and claims nothing stronger, and skipping
fsync-per-commit is what makes the rate viable at all.

**Concurrent reads fail through DuckDB and succeed through SQLite**, and the
reader is the variable rather than the distance behind the writer. The same
bounded batch, `pk > lo AND pk <= max(pk) - lag`, run five times each against
a 21 GB store while ingest wrote at 169 rows/s:

| reader | lag 100k | lag 1M | `MAX(pk)` probe | time |
|---|---|---|---|---|
| `sqlite3`, `mode=ro` | 5/5 | 5/5 | 10/10 | 0.02 s |
| DuckDB `sqlite_scanner` | 1/5 | 4/5 | 2/5 | ~9 s |

Failures are `database disk image is malformed`, and `PRAGMA quick_check`
returns `ok` afterwards, so the store is intact and the error is a read
artifact of WAL checkpointing. Lag reduces DuckDB's failure rate without
removing it. Write pressure is not the driver: an earlier run measured the
bounded read succeeding under 35k rows/s, 200x the rate that fails here. The
450x time difference points at the scanner reading far more of the file than
a rowid range needs.

**So the transform reads through SQLite**, which costs nothing it was not
already paying. The rows have to be materialized regardless — Pydantic
validates them row by row and routes them into per-collection arrays
([ADR-0011](0011-record-validation-and-routing-in-the-mart.md)) — so keeping
them inside DuckDB was never available. Measured on the real read, every
column including payload, `fetchall`'d while ingest wrote: a 15,000-row cycle
batch ([ADR-0014](0014-mart-grain-and-transform-cadence.md)) takes 0.023 s
and 8.2 MB, and 100,000 rows take 0.172 s and 53.6 MB, both 5/5. DuckLake
still takes the write.

The catalog is SQLite because DuckLake needs one and the alternatives do not
fit: DuckDB is single-client, and Postgres, the only backend with full
parallelism, is an external service
([catalog docs](https://ducklake.select/docs/stable/duckdb/usage/choosing_a_catalog_database)).
`sup` has three local processes and no external service. Keeping it in its
own file holds ingest's WAL churn away from catalog metadata I/O. Full
parallelism is the price; data inlining survives it, with 20,000 rows in
10-row transactions producing zero Parquet files and a
`ducklake_inlined_data_1_1` table.

Accepted costs:

- DuckLake and `sqlite` ship as DuckDB extensions fetched on first use, 36 MB
  and 35 MB into `~/.duckdb/`. The first run must reach
  `extensions.duckdb.org` as well as Jetstream, the files outlive
  `uv tool uninstall`, and installing both at bootstrap surfaces a blocked
  fetch at startup.
- Pruning must be ordered strictly after a verified DuckLake commit. Pruning
  before it is a data-loss path.
- The watermark state table is new state that must be correct; if it is
  wrong, the mart drifts silently rather than failing.
- The raw store's connection carries the 5-second `busy_timeout` default from
  `sqlite3.connect`.
