"""Schema creation and DuckDB extension install, run once per process at
startup.

Two stores, each with its own file under the shared data directory
(ADR-0002): `raw.db` holds durable ingested events in SQLite, and
`index.db` holds the DuckDB gap index the audit scans. Every statement is
idempotent, so startup repeats safely across restarts.
"""

from sup.db import SQLiteClient, DuckDBClient

# STRICT enforces declared column types on write. `events` is append-only,
# keyed solely on its autoincrement pk and carrying no secondary index; the
# M2 transform deduplicates on (did, rkey, rev). See ADR-0010.
RAW_SCHEMA = """CREATE TABLE IF NOT EXISTS events (
                pk INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                did TEXT NOT NULL,
                rkey TEXT NOT NULL,
                rev TEXT NOT NULL,
                time_us INTEGER NOT NULL,
                endpoint TEXT NOT NULL,
                tid_us INTEGER NOT NULL,
                payload TEXT NOT NULL
            ) STRICT;

            CREATE TABLE IF NOT EXISTS dlq (
                pk INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                error TEXT NOT NULL,
                payload TEXT NOT NULL
            ) STRICT;

            CREATE INDEX IF NOT EXISTS idx_dlq_timestamp ON dlq(timestamp);
        """
# Mirrors (pk, time_us) from raw.events for the gap scan. PRIMARY KEY on pk
# is the constraint the audit's INSERT OR REPLACE resolves against.
INDEX_SCHEMA = """CREATE TABLE IF NOT EXISTS gap_index (
    pk BIGINT PRIMARY KEY,
    time_us BIGINT NOT NULL
);
"""
async def bootstrap():
    """Create both schemas and install the mart's extensions, opening and
    closing each connection in turn."""
    async with SQLiteClient("raw.db") as client:
        await client.executescript(RAW_SCHEMA)
    async with DuckDBClient("index.db") as client:
        await client.conn.execute(INDEX_SCHEMA)
        # Repository extensions, downloaded into `~/.duckdb` on first use
        # and raising `IOException` when that fetch fails. Reinstalling an
        # extension already present takes about a millisecond and reaches
        # no network.
        await client.conn.execute("INSTALL ducklake")
        await client.conn.execute("INSTALL sqlite")
