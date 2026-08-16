"""Async database clients for the two stores described in ADR-0002.

Both wrap a driver that runs its blocking calls on a worker thread. Each
takes a filename and resolves it against the shared data directory.
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
        await self.cursor.execute("PRAGMA journal_mode=WAL;")
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

    def __init__(self, db):
        self.data_path = Config().data_path / db
        self.conn = None
        self.cursor = None
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
