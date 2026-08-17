# ADR-0025: Connection-local DuckDB state goes through `execute_on_self`

## Status

Accepted

## Options considered

- **`Connection.execute` throughout.** One call for every statement, matching
  the SQLite client's surface. Registered Arrow tables and `USE` are
  invisible to it.
- **A long-lived cursor held on the client**, with every statement issued
  against it. Keeps connection-local state in one place, and makes the
  client own a handle whose lifetime spans unrelated callers.
- **`execute_on_self` for the statements that read connection-local state,
  `execute` for the rest.** Chosen.

## Decision

`aioduckdb.Connection.execute` creates a cursor, which is a duplicate
connection. Registered Python objects and `USE` belong to the connection
that declared them, so a statement reading either runs through
`execute_on_self` or `as_cursor`, both of which use `self._conn`.

Committed rows cross handles freely. `GapAuditor._update_index` registers an
Arrow table and inserts it through `execute_on_self`, and `_scan_gaps` reads
the result back through `DuckDBClient.fetchall`, which is a different
duplicate connection. The mart transform's per-chunk Arrow flush takes the
same path.

Table names stay qualified as `sup_lake.main.<table>` rather than relying on
`USE`, so the mart's DDL and inserts are correct on any handle.

## Why

The failure is a catalog error naming the registered object, which reads as
a missing table rather than as a handle mismatch:

```
CatalogException: Catalog Error: Table with name tbl does not exist!
```

Measured against a 23.3M-row raw store, `register` followed by
`execute` fails on the first chunk in 0.19 s. The same loop with
`execute_on_self` mirrored 2,000,000 rows in 20 chunks of 100,000 in 2.4 s,
at 837,935 rows/s, with `gap_index` holding 2,000,000 rows over a contiguous
`pk` range. One registration per chunk holds for the whole loop.

The boundary is narrow, and stating it as "cursors share nothing" would
overshoot. Rows written on `_conn` are visible through a fresh cursor
immediately, including to the windowed `LAG` scan, because DuckDB
autocommits each statement and duplicated connections share the database
instance. Only the Python-side bindings are connection-local.

`aioduckdb` documents this at `core.py:211`, where `execute` carries the
warning that it does not copy over registered objects, and at `core.py:174`,
where `cursor` is described as a duplicate of the connection. Both are
docstrings on the call rather than anything the type signature shows.

Accepted costs:

- Two ways to run a statement, distinguished by whether it reads
  connection-local state. A wrong choice fails at the catalog rather than at
  the call.
- `register` holds its Arrow table alive until the name is rebound or
  released. Rebinding per chunk keeps one chunk resident: 16 bytes per row
  for `(pk, time_us)`, 408 bytes per row for the mart's columns.
- `DuckDBClient.fetchall` and `fetchone` each open a duplicate connection
  per call, so a caller needing connection-local state cannot use them.
