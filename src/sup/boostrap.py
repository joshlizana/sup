"""Schema creation, one function per service, called from that service's
own invocation (ADR-0022).

`bootstrap_ingest()` covers the two stores ingest writes: `raw.db` holds
durable ingested events and `index.db` the DuckDB gap index the audit scans.
`bootstrap_transform()` covers the two the transform writes: the DuckLake
mart, and `watermark.db` holding the committed position ingest prunes
against (ADR-0002, ADR-0023). Every statement is idempotent, so startup
repeats safely across restarts.
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
    """Create the raw store and gap index schemas, opening and closing each
    connection in turn."""
    async with SQLiteClient("raw.db") as client:
        await client.executescript(RAW_SCHEMA)
    async with DuckDBClient("index.db") as client:
        await client.conn.execute(INDEX_SCHEMA)

POSTS_SCHEMA = """CREATE TABLE IF NOT EXISTS sup_lake.main.posts (
k1            UBIGINT   NOT NULL,  -- hash of did||rkey||rev (ADR-0026)
k2            UBIGINT   NOT NULL,  -- the same string salted; the pair is one 128-bit key
did           VARCHAR   NOT NULL,  -- 14 to 41 bytes; did:plc: and did:web:
rkey          VARCHAR   NOT NULL,  -- 13-byte TID on all but 2 rows
rev           VARCHAR   NOT NULL,  -- 13-byte TID, no exceptions
operation     VARCHAR   NOT NULL,  -- create | update | delete
event_time    TIMESTAMP NOT NULL,  -- decoded from rev (ADR-0018)
time_us       UBIGINT   NOT NULL,  -- stream position
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
embed            JSON,         -- the embed subtree; external keeps uri,
                               -- title and description
facet_tags       VARCHAR[],    -- #tag features
facet_links      VARCHAR[],    -- #link features, the uri
facet_mentions   VARCHAR[]    -- #mention features, the did
);
"""
LIKES_SCHEMA = """CREATE TABLE IF NOT EXISTS sup_lake.main.likes (
k1            UBIGINT   NOT NULL,  -- hash of did||rkey||rev (ADR-0026)
k2            UBIGINT   NOT NULL,  -- the same string salted; the pair is one 128-bit key
did           VARCHAR   NOT NULL,  -- 14 to 41 bytes; did:plc: and did:web:
rkey          VARCHAR   NOT NULL,  -- 13-byte TID on all but 2 rows
rev           VARCHAR   NOT NULL,  -- 13-byte TID, no exceptions
operation     VARCHAR   NOT NULL,  -- create | update | delete
event_time    TIMESTAMP NOT NULL,  -- decoded from rev (ADR-0018)
time_us       UBIGINT   NOT NULL,  -- stream position
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
k1            UBIGINT   NOT NULL,  -- hash of did||rkey||rev (ADR-0026)
k2            UBIGINT   NOT NULL,  -- the same string salted; the pair is one 128-bit key
did           VARCHAR   NOT NULL,  -- 14 to 41 bytes; did:plc: and did:web:
rkey          VARCHAR   NOT NULL,  -- 13-byte TID on all but 2 rows
rev           VARCHAR   NOT NULL,  -- 13-byte TID, no exceptions
operation     VARCHAR   NOT NULL,  -- create | update | delete
event_time    TIMESTAMP NOT NULL,  -- decoded from rev (ADR-0018)
time_us       UBIGINT   NOT NULL,  -- stream position
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
k1            UBIGINT   NOT NULL,  -- hash of did||rkey||rev (ADR-0026)
k2            UBIGINT   NOT NULL,  -- the same string salted; the pair is one 128-bit key
did           VARCHAR   NOT NULL,  -- 14 to 41 bytes; did:plc: and did:web:
rkey          VARCHAR   NOT NULL,  -- 13-byte TID on all but 2 rows
rev           VARCHAR   NOT NULL,  -- 13-byte TID, no exceptions
operation     VARCHAR   NOT NULL,  -- create | update | delete
event_time    TIMESTAMP NOT NULL,  -- decoded from rev (ADR-0018)
time_us       UBIGINT   NOT NULL,  -- stream position
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
k1            UBIGINT   NOT NULL,  -- hash of did||rkey||rev (ADR-0026)
k2            UBIGINT   NOT NULL,  -- the same string salted; the pair is one 128-bit key
did           VARCHAR   NOT NULL,  -- 14 to 41 bytes; did:plc: and did:web:
rkey          VARCHAR   NOT NULL,  -- 13-byte TID on all but 2 rows
rev           VARCHAR   NOT NULL,  -- 13-byte TID, no exceptions
operation     VARCHAR   NOT NULL,  -- create | update | delete
event_time    TIMESTAMP NOT NULL,  -- decoded from rev (ADR-0018)
time_us       UBIGINT   NOT NULL,  -- stream position
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
k1            UBIGINT   NOT NULL,  -- hash of did||rkey||rev (ADR-0026)
k2            UBIGINT   NOT NULL,  -- the same string salted; the pair is one 128-bit key
did           VARCHAR   NOT NULL,
rkey          VARCHAR   NOT NULL,
rev           VARCHAR   NOT NULL,
collection    VARCHAR   NOT NULL,
received_at   TIMESTAMP NOT NULL,  -- when the ingest received it
processed_at  TIMESTAMP NOT NULL,  -- when the transform processed it
time_us       UBIGINT   NOT NULL,
error         VARCHAR   NOT NULL,
payload       JSON      NOT NULL
);
"""
WATERMARK_SCHEMA = """PRAGMA auto_vacuum=INCREMENTAL;
                      PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS watermark (
    enforcer INTEGER DEFAULT 1 CHECK (enforcer = 1) PRIMARY KEY,
    watermark INTEGER NOT NULL
) STRICT;
"""
async def bootstrap_transform():
    """Create the mart schema and the watermark store, opening and closing
    each connection in turn."""
    async with DucklakeClient() as client:
        schemas = [POSTS_SCHEMA, LIKES_SCHEMA, REPOSTS_SCHEMA, FOLLOWS_SCHEMA, BLOCKS_SCHEMA, REJECTS_SCHEMA]
        for schema in schemas:
            await client.conn.execute(schema)

    async with SQLiteClient("watermark.db") as client:
        await client.executescript(WATERMARK_SCHEMA)
