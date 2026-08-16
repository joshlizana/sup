"""Every-start gap detection (TDD-0003 §1).

Compares what the raw store already holds against the window Jetstream
still serves, and produces the list of time ranges worth reading. Runs to
completion before any ingest worker starts.

Each step of `run()` raises on failure rather than returning a partial
result. Endpoint probes are the exception: one that fails drops out of the
floor comparison, and the configured retention stands when all of them
fail.
"""

from sup.config import Config
from sup.util import register, displayTime
from sup.db import DuckDBClient, SQLiteClient
from sup.services.ingest.jetstream import JetstreamClient
import logging
import asyncio
import pyarrow
import time
import sys


# Gap detection runs in DuckDB against `gap_index`, a mirror of
# (pk, time_us) read from the raw store through SQLite and inserted
# columnar. Each run copies forward from the highest pk already mirrored,
# so the read is a rowid seek and finds nothing when ingest has not moved
# (ADR-0002).
TEN_SECONDS = 10_000_000
GET_MAX_PK_SQL = "SELECT COALESCE(MAX(pk), 0) FROM gap_index"
GET_NEW_INDEX_SQL = "SELECT pk, time_us FROM events WHERE pk > ? ORDER BY pk"
UPDATE_INDEX_SQL = "INSERT OR REPLACE INTO gap_index (pk, time_us) FROM tbl"
GAP_SCAN_SQL = f"""WITH SortedGaps as (
                SELECT LAG(time_us) OVER (ORDER BY time_us, pk) as prev_time_us, time_us 
                FROM gap_index
                WHERE time_us >= ?)
                SELECT prev_time_us, time_us FROM SortedGaps
                WHERE (time_us - prev_time_us) > {TEN_SECONDS} 
                ORDER BY prev_time_us""" 
EARLIEST_CURSOR_SQL = "SELECT COALESCE(MIN(time_us), 0) FROM gap_index WHERE time_us >= ?"
LATEST_CURSOR_SQL = "SELECT COALESCE(MAX(time_us), 0) FROM gap_index WHERE time_us >= ?"

class GapAuditor:
    def __init__(self):
        self.NOW: int = int(time.time() * 1_000_000)
        self.duckdb_client: DuckDBClient | None = None
        self.sqlite_client: SQLiteClient | None = None
        self.endpoints: list[str] = Config().endpoints
        self.retention_floor: int | None = Config().retention
        self.gaps: list[tuple[int, int]] = []
        self.log: logging.Logger = register(logging.getLogger(__name__), self.__class__.__name__)

    async def __aenter__(self):
        """Open both stores: the DuckDB index the scan runs against, and
        the raw store the mirror reads from."""
        self.duckdb_client = DuckDBClient("index.db")
        self.sqlite_client = SQLiteClient("raw.db")
        await self.duckdb_client.__aenter__()
        await self.sqlite_client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.duckdb_client.__aexit__(exc_type, exc_val, exc_tb)
        await self.sqlite_client.__aexit__(exc_type, exc_val, exc_tb)

    async def run(self):
        """Return the sharded ranges for the ingest workers to claim.

        Establishes the retention floor, refreshes the index, finds the
        interior gaps, adds the leading and trailing ranges, and splits the
        result into shards.
        """
        try:
            self.log.info("Starting audit run")
            await self._update_retention_floor()
            await self._update_index()
            await self._scan_gaps()
            await self._aggregate_gaps()
            await self._shard_gaps()
            self.log.info("Audit run completed")
        except Exception as e:
            self.log.error(f"Error in audit run: {e}")
            raise
        return self.gaps

    async def _update_retention_floor(self):
        """Set `retention_floor` from concurrent probes of every endpoint.

        Takes the oldest position any endpoint reports, then applies the
        configured retention as a ceiling: whichever of the two is more
        recent becomes the floor. Leaves the configured value in place when
        no endpoint answers.
        """
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(self._probe(endpoint[0])) for endpoint in self.endpoints]

        results = [task.result() for task in tasks]
        filtered_results = [r for r in results if r is not None]
        oldest_retention = min(filtered_results) if filtered_results else None
        self.retention_floor = oldest_retention if oldest_retention is not None and oldest_retention > self.retention_floor else self.retention_floor
        self.log.info(f"Updated retention floor to {displayTime(self.retention_floor)}")

    async def _probe(self, endpoint: str):
        """Return one endpoint's retention floor, or None on any failure or
        after 15 seconds."""
        try:
            async with asyncio.timeout(15):
                async with JetstreamClient(endpoint, 0) as client:
                    return (await client.get_endpoint_health())[0]
        except Exception as e:
            self.log.error(f"Error probing endpoint {endpoint}: {e}")
            return None

    async def _update_index(self):
        """Copy events newer than the index's high-water mark into it.

        Reads in chunks and inserts each before reading the next, so
        neither the row list nor the Arrow table grows with the backlog.
        Each chunk commits on its own, so a mirror interrupted partway
        keeps what it wrote and the next run resumes above it — which
        holds because the rows arrive in `pk` order.
        """
        try:
            max_pk = (await self.duckdb_client.fetchone(GET_MAX_PK_SQL))[0]
            async with self.sqlite_client.conn.execute(GET_NEW_INDEX_SQL, (max_pk,)) as cursor:
                while chunk := await cursor.fetchmany(100_000):
                    pk, time_us = zip(*chunk)
                    tbl = pyarrow.Table.from_arrays([pyarrow.array(pk), pyarrow.array(time_us)], names=["pk", "time_us"])
                    await self.duckdb_client.conn.register("tbl", tbl)
                    await self.duckdb_client.conn.execute(UPDATE_INDEX_SQL)

        except Exception as e:
            self.log.error(f"Error updating index: {e}")
            raise

    async def _scan_gaps(self):
        """Collect interior gaps: pairs of consecutive stored events more
        than ten seconds apart.

        The oldest row in the window has no predecessor, which the first
        row's null check identifies. An empty result means a continuous
        window.
        """
        try:
            gaps = await self.duckdb_client.fetchall(GAP_SCAN_SQL, (self.retention_floor,))

            if gaps and gaps[0][0] is not None:
                self.gaps.extend(gaps)
        except Exception as e:
            self.log.error(f"Error scanning gaps: {e}")
            raise
        self.log.info(f"Found gaps: {len(self.gaps)}")

    async def _aggregate_gaps(self):
        """Add the ranges at the edges of the stored window.

        An empty index reports both cursors as zero and yields one gap
        covering the whole recoverable range. Otherwise a leading range
        covers the floor up to the earliest stored event, and a trailing
        range covers the newest stored event up to now.
        """
        try:
            first_cursor = (await self.duckdb_client.fetchone(EARLIEST_CURSOR_SQL, (self.retention_floor,)))[0]
            latest_cursor = (await self.duckdb_client.fetchone(LATEST_CURSOR_SQL, (self.retention_floor,)))[0]
        except Exception as e:
            self.log.error(f"Error retrieving first and last cursors: {e}")
            raise

        if len(self.gaps) == 0 and first_cursor == 0 and latest_cursor == 0:
            self.gaps.append((self.retention_floor, self.NOW))

        if first_cursor != 0 and self.retention_floor < first_cursor:
            self.gaps.append((self.retention_floor, first_cursor))

        if latest_cursor != 0 and latest_cursor < self.NOW:
            self.gaps.append((latest_cursor, self.NOW))

        self.log.info(f"Aggregated gaps: {len(self.gaps)}")


    async def _shard_gaps(self):
        """Split each gap into 30-minute ranges and append the live tail.

        Every shard is widened by ten seconds at each edge, so boundary
        events arrive twice. The tail range runs from now to `sys.maxsize`
        and sorts last in the priority queue.
        """
        sharded_gaps = []
        THIRTY_MINUTES = 30 * 6 * TEN_SECONDS
        for start, end in self.gaps:
            while end - start > THIRTY_MINUTES: 
                sharded_gaps.append((start - TEN_SECONDS, start + THIRTY_MINUTES + TEN_SECONDS)) 
                start = start + THIRTY_MINUTES + TEN_SECONDS
            sharded_gaps.append((start - TEN_SECONDS, end + TEN_SECONDS))

        sharded_gaps.append((self.NOW - TEN_SECONDS, sys.maxsize)) 
        self.gaps = sharded_gaps

        self.log.info(f"Sharded gaps: {len(self.gaps)}")

