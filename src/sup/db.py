"""Async database clients for the stores described in ADR-0002.

Each wraps a driver that runs its blocking calls on a worker thread.
`SQLiteClient` and `DuckDBClient` take a filename and resolve it against the
shared data directory, covering `raw.db`, `index.db` and `watermark.db`;
`DucklakeClient` attaches the mart, whose location is fixed.
"""

import aioduckdb
import aiosqlite
from sup.util import register
from sup.config import Config
import logging

class SQLiteClient:
    """Connection to the durable raw store.

    Holds one connection and one cursor for its lifetime; the cursor backs
    `bulk_insert()`.
    """

    def __init__(self, db):
        self.data_path = Config().data_path / db
        self.conn = None
        self.cursor = None
        self.log = register(logging.getLogger(__name__), self.__class__.__name__)

    async def __aenter__(self):
        """Open the connection in WAL mode with `synchronous=NORMAL`, the
        durability settings ADR-0002 specifies."""
        self.log.info("Initializing SQLite client")
        self.conn = await aiosqlite.connect(self.data_path)
        self.cursor = await self.conn.cursor()
        await self.cursor.execute("PRAGMA synchronous=NORMAL;")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.log.info("Exiting SQLite client")
        if self.conn is not None:
            await self.conn.close()

    async def fetchall(self, query: str, params: tuple = ()):
        async with self.conn.execute(query, params) as cursor:
            return await cursor.fetchall()

    async def fetchone(self, query: str, params: tuple = ()):
        async with self.conn.execute(query, params) as cursor:
            return await cursor.fetchone()

    async def executescript(self, script: str):
        """Run a multi-statement SQL script, used for schema creation."""
        await self.conn.executescript(script)

    async def bulk_insert(self, query: str, params_list: list):
        """Insert a batch as one transaction and commit it.

        Rows become durable at the commit. A failure rolls the batch back
        before re-raising, so the batch lands whole or not at all.
        """
        try:    
            await self.cursor.executemany(query, params_list)
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise


class DuckDBClient:
    """Connection to the DuckDB gap index, used by `GapAuditor` for the
    windowed gap scan."""

    def __init__(self, db: str):
        self.data_path = Config().data_path / db
        self.conn: aioduckdb.Connection | None = None
        self.cursor: aioduckdb.Cursor | None = None
        self.log = register(logging.getLogger(__name__), self.__class__.__name__)

    async def __aenter__(self):
        self.log.info("Initializing DuckDB client")
        self.conn = await aioduckdb.connect(self.data_path)
        self.cursor = await self.conn.cursor()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.log.info("Exiting DuckDB client")
        if self.conn is not None:
            await self.conn.close()

    async def fetchall(self, query: str, params: tuple = ()):
        async with self.conn.execute(query, params) as cursor:
            return await cursor.fetchall()

    async def fetchone(self, query: str, params: tuple = ()):
        async with self.conn.execute(query, params) as cursor:
            return await cursor.fetchone()

class DucklakeClient:
    """Connection to the mart.

    The DuckDB instance is in-memory and holds no data: it is the handle
    the DuckLake catalog attaches to, and every table lives in the lake
    (ADR-0002). The catalog is SQLite, selected by the `sqlite:` prefix,
    with Parquet under `DATA_PATH`.
    """

    def __init__(self):
        self.data_path = Config().ducklake_path
        self.conn = None
        self.cursor = None
        self.log = register(logging.getLogger(__name__), self.__class__.__name__)

    async def __aenter__(self):
        self.log.info("Initializing Ducklake client")
        self.conn = await aioduckdb.connect(':memory:')
        # Repository extensions, downloaded into `~/.duckdb` on first use
        # and raising `IOException` when that fetch fails. Installing ahead
        # of the LOAD puts a blocked fetch at the connection rather than
        # inside a transform cycle. Reinstalling an extension already
        # present takes about a millisecond and reaches no network.
        await self.conn.execute("INSTALL ducklake")
        await self.conn.execute("INSTALL sqlite")
        await self.conn.execute("LOAD ducklake;")
        await self.conn.execute("LOAD sqlite;")
        await self.conn.execute(f"ATTACH 'ducklake:sqlite:{self.data_path}/sup.ducklake' AS sup_lake ( DATA_PATH '{self.data_path}' );")
        self.cursor = await self.conn.cursor()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.log.info("Exiting Ducklake client")
        if self.conn is not None:
            await self.conn.close()
