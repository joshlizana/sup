# ADR-0022: Each service bootstraps its own store

## Status

Accepted

## Options considered

- **One `bootstrap()` at the entry point, raw stores only.** `main()` creates
  the `raw.db` and `index.db` schemas before Typer dispatches. It runs for
  `sup --help`, and under
  [TDD-0002](../tdd/0002-cli-orchestration.md) it runs again in each of the
  four subprocesses, none of which share a store.
- **Add the mart tables to that same `bootstrap()`.** One module owns every
  schema and the DuckLake tables exist before the transform's first insert.
  It also hands a mart handle and a `CREATE TABLE` to ingest, the dashboard
  and the TUI.
- **A bootstrap function per service, called from that service's
  invocation.** One module keeps every schema definition, and each function
  runs in the one process that owns the store. Chosen.

## Decision

`boostrap.py` holds one function per service and `main()` hands straight to
the Typer app.

- `bootstrap_ingest()` creates the `raw.db` and `index.db` schemas. `sup
  ingest` calls it.
- `bootstrap_transform()` attaches the DuckLake catalog and creates the five
  per-collection tables and the reject table
  ([TDD-0004](../tdd/0004-mart-schema.md)). The transform calls it.

`bootstrap_transform()` installs and loads the `ducklake` and `sqlite`
extensions. The transform is the only service holding a DuckLake handle
([ADR-0023](0023-committed-position-in-sqlite.md)), and a SQLite-backed
catalog needs the `sqlite` extension to attach at all. Both are repository
extensions fetched into `~/.duckdb` on first use, so a blocked download
fails at the transform's startup.

Creating the mart tables belongs to the transform, matching its role as the
mart's only writer ([ADR-0013](0013-service-owned-pruning.md)). Ingest
attaches the mart to read.

## Why

`bootstrap()` at the entry point runs ahead of Typer's dispatch, so it
executes for every invocation of the console script, `sup --help` included.
Under orchestration each service is a fresh `sup <subcommand>` process, so
four processes run the same schema creation against stores three of them
have no business touching.

DDL is a write. Putting the mart tables in a shared bootstrap makes ingest,
the dashboard and the TUI writers of a store
[ADR-0013](0013-service-owned-pruning.md) grants exactly one writer, and
hands the TUI a database connection
[ADR-0015](0015-tui-as-control-plane-client.md) denies it. `ATTACH` on a
DuckLake catalog creates the catalog file and its Parquet data directory,
so the invocation that only wanted `--help` leaves a mart behind.

Concurrency is the weaker argument and is recorded as a risk, not a
measurement: TDD-0002 starts the four services in sequence and their
bootstraps overlap, which would put concurrent DDL on one DuckLake catalog.
The catalog-desync reports in the DuckLake tracker cover large writes and
`DROP TABLE`; concurrent `CREATE TABLE IF NOT EXISTS` is untested here and
belongs to the same unvalidated family as
[ADR-0002](0002-raw-ingestion-durability.md)'s cross-process risk.

Accepted costs:

- On a first run the mart catalog is absent until the transform creates it,
  and TDD-0002 starts ingest first. Ingest's first maintenance passes find
  no committed position and skip the prune until the transform has run once.
- Each service added later brings its own bootstrap function and the
  discipline of calling it.
- A subcommand that forgets the call meets a missing table at its first
  query, where a shared bootstrap would have covered it.
