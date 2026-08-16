"""Batched writer for the durable raw store.

The single consumer of both message queues and the only component that
writes to `raw.db`. Each pass drains what the readers have produced and
commits it as one transaction.
"""

from sup.db import SQLiteClient
from sup.util import register
import asyncio
import logging


# Passing null for the autoincrement pk lets SQLite assign the strictly
# increasing key the mart's watermark advances on.
INSERT_DLQ = "INSERT into dlq (pk, received_at, error, payload) VALUES (null, ?, ?, ?)"

# `events` carries no constraint to resolve against, so a failing insert
# raises (ADR-0010).
INSERT_EVENT = "INSERT into events (pk, received_at, did, rkey, rev, time_us, endpoint, tid_us, payload) VALUES (null, ?, ?, ?, ?, ?, ?, ?, ?)"

class Writer:
    def __init__(self, message_queue: asyncio.Queue, dlq_queue: asyncio.Queue):
        self.message_queue = message_queue
        self.dlq_queue = dlq_queue
        self.db_client = None
        self.running = True
        self.log = register(logging.getLogger(__name__), self.__class__.__name__)

    async def __aenter__(self):
        self.db_client = SQLiteClient("raw.db")
        await self.db_client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.db_client:
            await self.db_client.__aexit__(exc_type, exc_val, exc_tb)

    async def run(self):
        """Commit whatever the readers produce until stopped.

        Flushes on every pass that finds work, sleeping 10ms when both
        queues are empty. Flushes both queues once more after the loop.
        """
        self.log.info("Writer started")
        while self.running:
            if self.dlq_queue.qsize() > 0:
                await self.flush_dlq()
            if self.message_queue.qsize() > 0:
                await self.flush_message_queue()

            if self.dlq_queue.qsize() == 0 and self.message_queue.qsize() == 0:
                await asyncio.sleep(0.01)
        await self.flush_dlq()
        await self.flush_message_queue()
        self.log.info("Writer stopped")

    async def flush_dlq(self):
        """Commit the queued payloads that failed to parse.

        A failed insert is logged with the batch size, and the batch is
        dropped.
        """
        flush = []

        for _ in range(self.dlq_queue.qsize()):
            flush.append(await self.dlq_queue.get())

        retry = 0
        while flush and retry < 3:
            try:
                await self.db_client.bulk_insert(INSERT_DLQ, flush)
                break
            except Exception as e:
                self.log.error(f"Error flushing {len(flush)} items from the DLQ: {e}")
                retry += 1

    async def flush_message_queue(self):
        """Commit the queued events.

        Drains exactly `qsize()` items, leaving anything that arrives
        mid-flush for the next pass. A failed insert is logged with the
        batch size, and the batch is dropped.
        """
        flush = []

        for _ in range(self.message_queue.qsize()):
            flush.append(await self.message_queue.get())

        retry = 0
        while flush and retry < 3:
            try:
                await self.db_client.bulk_insert(INSERT_EVENT, flush)
                break
            except Exception as e:
                self.log.error(f"Error flushing {len(flush)} items from the message queue: {e}")
                retry += 1