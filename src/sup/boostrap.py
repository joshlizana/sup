"""Schema creation and DuckDB extension install, run once per process at
startup.

Two stores, each with its own file under the shared data directory
(ADR-0002): `raw.db` holds durable ingested events in SQLite, and
`index.db` holds the DuckDB gap index the audit scans. Every statement is
idempotent, so startup repeats safely across restarts.
"""

from sup.db import SQLiteClient, DuckDBClient, DucklakeClient

# STRICT enforces declared column types on write. `events` is append-only,
# keyed solely on its autoincrement pk and carrying no secondary index; the
# M2 transform deduplicates on (did, rkey, rev). See ADR-0010.
RAW_SCHEMA = """PRAGMA auto_vacuum=INCREMENTAL;
                PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS events (
                pk INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at INTEGER NOT NULL,
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
                received_at INTEGER NOT NULL,
                error TEXT NOT NULL,
                payload TEXT NOT NULL
            ) STRICT;

            CREATE INDEX IF NOT EXISTS idx_dlq_received_at ON dlq(received_at);
        """
# Mirrors (pk, time_us) from raw.events for the gap scan. PRIMARY KEY on pk
# is the constraint the audit's INSERT OR REPLACE resolves against.
INDEX_SCHEMA = """CREATE TABLE IF NOT EXISTS gap_index (
    pk BIGINT PRIMARY KEY,
    time_us BIGINT NOT NULL
);
"""
async def bootstrap_ingest():
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

POSTS_SCHEMA = """CREATE TABLE IF NOT EXISTS sup_lake.main.posts (
did           VARCHAR   NOT NULL,  -- 14 to 41 bytes; did:plc: and did:web:
rkey          VARCHAR   NOT NULL,  -- 13-byte TID on all but 2 rows
rev           VARCHAR   NOT NULL,  -- 13-byte TID, no exceptions
operation     VARCHAR   NOT NULL,  -- create | update | delete
event_time    TIMESTAMP NOT NULL,  -- decoded from rev (ADR-0018)
time_us       BIGINT    NOT NULL,  -- stream position
endpoint      VARCHAR   NOT NULL,  -- the connection that witnessed it
received_at   TIMESTAMP NOT NULL,  -- when the ingest received it
processed_at  TIMESTAMP NOT NULL,  -- when the transform processed it
cid           VARCHAR,             -- NULL on deletes
created_at    TIMESTAMP,           -- client-supplied, NULL on deletes
text             VARCHAR,      -- 100%, up to 1,156 bytes
langs            VARCHAR[],    -- 88.90%, one element in almost all cases
reply_root_uri   VARCHAR,      -- 45.54%
reply_root_cid   VARCHAR,
reply_parent_uri VARCHAR,
reply_parent_cid VARCHAR,
embed_type       VARCHAR,      -- 37.17%
embed            JSON,         -- the embed subtree, verbatim
facet_tags       VARCHAR[],    -- #tag features
facet_links      VARCHAR[],    -- #link features, the uri
facet_mentions   VARCHAR[],    -- #mention features, the did
tags             VARCHAR[],    -- 0.94%, record-level, distinct from facets
self_labels      VARCHAR[],    -- 1.33%, labels.values[].val
via              VARCHAR       -- 0.57%, a client name such as "TOKIMEKI"
);
"""
LIKES_SCHEMA = """CREATE TABLE IF NOT EXISTS sup_lake.main.likes (
did           VARCHAR   NOT NULL,  -- 14 to 41 bytes; did:plc: and did:web:
rkey          VARCHAR   NOT NULL,  -- 13-byte TID on all but 2 rows
rev           VARCHAR   NOT NULL,  -- 13-byte TID, no exceptions
operation     VARCHAR   NOT NULL,  -- create | update | delete
event_time    TIMESTAMP NOT NULL,  -- decoded from rev (ADR-0018)
time_us       BIGINT    NOT NULL,  -- stream position
endpoint      VARCHAR   NOT NULL,  -- the connection that witnessed it
received_at   TIMESTAMP NOT NULL,  -- when the ingest received it
processed_at  TIMESTAMP NOT NULL,  -- when the transform processed it
cid           VARCHAR,             -- NULL on deletes
created_at    TIMESTAMP,           -- client-supplied, NULL on deletes
subject_uri        VARCHAR,
subject_cid        VARCHAR,
subject_collection VARCHAR,   -- the NSID parsed out of subject_uri
via_uri            VARCHAR,   -- 17.73%, the repost the like came through
via_cid            VARCHAR
);
"""
REPOSTS_SCHEMA = """CREATE TABLE IF NOT EXISTS sup_lake.main.reposts (
did           VARCHAR   NOT NULL,  -- 14 to 41 bytes; did:plc: and did:web:
rkey          VARCHAR   NOT NULL,  -- 13-byte TID on all but 2 rows
rev           VARCHAR   NOT NULL,  -- 13-byte TID, no exceptions
operation     VARCHAR   NOT NULL,  -- create | update | delete
event_time    TIMESTAMP NOT NULL,  -- decoded from rev (ADR-0018)
time_us       BIGINT    NOT NULL,  -- stream position
endpoint      VARCHAR   NOT NULL,  -- the connection that witnessed it
received_at   TIMESTAMP NOT NULL,  -- when the ingest received it
processed_at  TIMESTAMP NOT NULL,  -- when the transform processed it
cid           VARCHAR,             -- NULL on deletes
created_at    TIMESTAMP,           -- client-supplied, NULL on deletes
subject_uri        VARCHAR,
subject_cid        VARCHAR,
subject_collection VARCHAR,   -- the NSID parsed out of subject_uri
via_uri            VARCHAR,   -- 29.20%, the repost this one came through
via_cid            VARCHAR
);
"""
FOLLOWS_SCHEMA = """CREATE TABLE IF NOT EXISTS sup_lake.main.follows (
did           VARCHAR   NOT NULL,  -- 14 to 41 bytes; did:plc: and did:web:
rkey          VARCHAR   NOT NULL,  -- 13-byte TID on all but 2 rows
rev           VARCHAR   NOT NULL,  -- 13-byte TID, no exceptions
operation     VARCHAR   NOT NULL,  -- create | update | delete
event_time    TIMESTAMP NOT NULL,  -- decoded from rev (ADR-0018)
time_us       BIGINT    NOT NULL,  -- stream position
endpoint      VARCHAR   NOT NULL,  -- the connection that witnessed it
received_at   TIMESTAMP NOT NULL,  -- when the ingest received it
processed_at  TIMESTAMP NOT NULL,  -- when the transform processed it
cid           VARCHAR,             -- NULL on deletes
created_at    TIMESTAMP,           -- client-supplied, NULL on deletes
subject  VARCHAR,   -- a DID, did:plc: or did:web:
via_uri  VARCHAR,   -- 15.88%, the starter pack the follow came through
via_cid  VARCHAR
);
"""
BLOCKS_SCHEMA = """CREATE TABLE IF NOT EXISTS sup_lake.main.blocks (
did           VARCHAR   NOT NULL,  -- 14 to 41 bytes; did:plc: and did:web:
rkey          VARCHAR   NOT NULL,  -- 13-byte TID on all but 2 rows
rev           VARCHAR   NOT NULL,  -- 13-byte TID, no exceptions
operation     VARCHAR   NOT NULL,  -- create | update | delete
event_time    TIMESTAMP NOT NULL,  -- decoded from rev (ADR-0018)
time_us       BIGINT    NOT NULL,  -- stream position
endpoint      VARCHAR   NOT NULL,  -- the connection that witnessed it
received_at   TIMESTAMP NOT NULL,  -- when the ingest received it
processed_at  TIMESTAMP NOT NULL,  -- when the transform processed it
cid           VARCHAR,             -- NULL on deletes
created_at    TIMESTAMP,           -- client-supplied, NULL on deletes
subject  VARCHAR,   -- a DID, did:plc: or did:web:
via_uri  VARCHAR,   -- unset on all 282,042 block records measured
via_cid  VARCHAR
);
"""
REJECTS_SCHEMA = """CREATE TABLE IF NOT EXISTS sup_lake.main.rejects (
did           VARCHAR,
rkey          VARCHAR,
rev           VARCHAR,
collection    VARCHAR,
received_at   TIMESTAMP NOT NULL,  -- when the ingest received it
processed_at  TIMESTAMP NOT NULL,  -- when the transform processed it
time_us       BIGINT NOT NULL,
error         VARCHAR NOT NULL,
payload       JSON NOT NULL
);
"""
WATERMARK_SCHEMA = """CREATE TABLE IF NOT EXISTS sup_lake.main.watermark (
    watermark BIGINT NOT NULL
);
"""
async def bootstrap_transform():
    """Create the transform's schema, opening and closing the connection."""
    async with DucklakeClient() as client:
        schemas = [POSTS_SCHEMA, LIKES_SCHEMA, REPOSTS_SCHEMA, FOLLOWS_SCHEMA, BLOCKS_SCHEMA, REJECTS_SCHEMA, WATERMARK_SCHEMA]
        for schema in schemas:
            await client.conn.execute(schema)
