# ADR-0023: The committed position is its own SQLite store

## Status

Accepted

## Options considered

- **A watermark table in the mart.** The transform is already the mart's only
  writer, so publishing there adds no store and lets rows and position commit
  in one DuckLake transaction. It puts a DuckDB-mediated read of a
  concurrently-written SQLite catalog on ingest's maintenance path, and asks a
  snapshot format to serve a counter.
- **A watermark table in the raw store.** One fewer file and ingest reads it
  through the connection it already holds. The transform becomes a second
  writer of `raw.db`, which [ADR-0013](0013-service-owned-pruning.md) exists
  to prevent.
- **Its own SQLite file.** A store whose whole content is one row, written by
  the transform and read by ingest through `aiosqlite`. Chosen.

## Decision

`watermark.db` sits beside `raw.db` and `index.db` under `Config.data_path`.
The transform is its only writer; ingest reads it through `aiosqlite` on each
maintenance pass and prunes `events` below it
([ADR-0021](0021-service-maintenance.md)).

The transform commits the mart first and writes the position second. A crash
between the two leaves the position behind the data, which the next cycle
reprocesses and the mart deduplicates
([ADR-0010](0010-deduplication-in-the-mart.md)).

`watermark.db` is part of `sup clean`'s wipe
([ADR-0008](0008-sup-clean-full-reset.md)), which is the one path that
discards it.

## Why

SQLite is built for small frequent point writes and DuckLake is built for
columnar scans over immutable files. The committed position is a counter
overwritten once per cycle, which is the first profile.

Measured against a DuckLake catalog over 5,000 single-row updates: each write
takes 11.7 ms at 500 accumulated snapshots and 13.9 ms at 5,000, the catalog
grows 0.13 KB per snapshot, and no Parquet file is written, because DuckLake
inlines a row that small. `ducklake_expire_snapshots` and
`ducklake_cleanup_old_files` collapse 5,003 snapshots to 1 in 0.08 s, and the
catalog file returns from 788 KB to 228 KB on a SQLite `VACUUM`. A full
backfill runs about 1,933 cycles, so the accepted cost of the mart-side table
is roughly 245 KB and 24 s of write time.

Concurrency decides it. Ingest reading the position out of the mart is a
DuckDB-mediated read of a SQLite file the transform is writing, which is the
shape [ADR-0002](0002-raw-ingestion-durability.md) measured failing
intermittently with `database disk image is malformed` at any watermark
distance, against SQLite's own connection succeeding 5/5. The transform
already reads `raw.db` through `aiosqlite` for that reason, and ingest reads
`watermark.db` the same way.

Ingest holds no DuckLake handle, so the `ducklake` and `sqlite` extensions
belong to the transform alone ([ADR-0022](0022-per-service-bootstrap.md)).

Accepted costs:

- A fourth store, with its own place in `sup clean` and in any reset.
- The position survives a mart delete, and the raw rows it points past are
  gone. Discarding the mart means a full reset through `sup clean`
  ([ADR-0008](0008-sup-clean-full-reset.md)), which clears both.
- One DuckLake transaction covering rows and position becomes an ordering
  rule across two stores. The safe order is mart, then position.
- Two SQLite files are open in ingest's maintenance pass, on top of the DuckDB
  handle for `gap_index`.
