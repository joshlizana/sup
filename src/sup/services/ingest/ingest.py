"""Ingest service orchestrator.

Acquires the single-instance lock, audits the raw store for gaps, seeds a
work queue with the resulting shards, and drives one `Reader` per endpoint
feeding one `Writer`. Supplies the quiesce, resume, and shutdown hooks
`Controller` dispatches.
"""

import asyncio
import logging
from sup.util import register
from sup.config import Config
from sup.services.ingest.reader import Reader
from sup.services.ingest.writer import Writer
from sup.services.ingest.audit import GapAuditor
from sup.control.controller import Controller

class Ingester:
    def __init__(self):
        self.running: bool = True
        self.paused: bool = False
        self.controller: Controller = Controller("ingest", self.quiesce, self.resume, self.shutdown, self.return_status)
        self.auditor: GapAuditor = GapAuditor()
        self.readers: list[Reader] = []
        self.reader_tasks: list[asyncio.Task] = []
        self.backfill_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.message_queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self.dlq_queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self.writer: Writer = Writer(self.message_queue, self.dlq_queue)
        self.writer_task: asyncio.Task = None
        self.log: logging.Logger = register(logging.getLogger(__name__), self.__class__.__name__)

    async def __aenter__(self):
        """Acquire the control plane, the auditor, and the writer, then
        construct one `Reader` per configured endpoint. Raises if another
        instance holds the lock."""
        
        self.log.info("Acquiring control plane")
        await self.controller.__aenter__()
        self.log.info("Acquiring auditor")
        await self.auditor.__aenter__()
        self.log.info("Acquiring writer")
        await self.writer.__aenter__()
        self.readers = [await Reader(endpoint, self.backfill_queue, self.message_queue, self.dlq_queue).__aenter__() for endpoint in Config().endpoints]
        self.log.info(f"Created readers for {len(Config().endpoints)} endpoints")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Release the writer, then the control plane. `run()` releases the
        auditor once the shards are in hand."""
        self.log.info("Releasing writer")
        await self.writer.__aexit__(exc_type, exc_val, exc_tb)
        self.log.info("Releasing control plane")
        await self.controller.__aexit__(exc_type, exc_val, exc_tb)

    async def run(self):
        """Run the audit, start the workers, and idle until shutdown.

        Releases the auditor's connection once the shards are in hand.
        Worker tasks start when the run is active and unpaused, so a pause
        or shutdown arriving during the audit leaves them unstarted. Drains
        on the way out.
        """
        self.log.info("Running ingest")
        self.log.info("Beginning gap audit")
        shards = await self.auditor.run()
        await self.auditor.__aexit__(None, None, None)
        self.log.info("Gap audit complete. Auditor released")

        # Ordered by range start, so the oldest range is claimed first and
        # the live tail — starting at "now" — is claimed last.
        for shard in shards:
            await self.backfill_queue.put(shard)

        if self.running and not self.paused:
            self.writer_task = asyncio.create_task(self.writer.run())
            self.reader_tasks = []
            for reader in self.readers:
                self.reader_tasks.append(asyncio.create_task(reader.run()))
            self.log.info(f"All {len(self.reader_tasks) + 1} tasks initialized")

        cycles = 0
        while self.running:
            cycles += 1
            if cycles % 100 == 0:
                throughput = sum(reader.throughput for reader in self.readers)
                readers = sum(1 for reader in self.readers if reader.reading)
                writer = "Up" if (self.writer_task is not None and not self.writer_task.done()) else "Down"
                self.log.info(f"Status: readers:{readers}/{len(self.readers)} throughput:{throughput} events/sec backfill:{self.backfill_queue.qsize()} writer:{writer} msgq:{self.message_queue.qsize()} dlq:{self.dlq_queue.qsize()}")

            await asyncio.sleep(0.1)

        await self.drain()
        self.log.info("Service shutdown complete")


    async def return_status(self):
        """Return the service's current status as a dict (ADR-0015).

        `Controller` runs each connection handler on the loop that owns
        this service, so these reads need no synchronisation, and it adds
        its own lifecycle `status` before replying.
        """
        throughput = sum(reader.throughput for reader in self.readers)
        readers = sum(1 for reader in self.readers if reader.reading)
        writer = "Up" if (self.writer_task is not None and not self.writer_task.done()) else "Down"
        return {
            "active_readers": readers,
            "total_readers": len(self.readers),
            "throughput": throughput,
            "backfill": self.backfill_queue.qsize(),
            "writer": writer,
            "msgq": self.message_queue.qsize(),
            "dlq": self.dlq_queue.qsize(),
        }


    async def quiesce(self):
        """Drain the workers and mark the run paused. The lock and control
        socket stay held."""
        self.log.info("Received pause command")
        await self.drain()
        self.paused = True
        self.log.info("Service paused")

    async def resume(self):
        """Start a fresh generation of worker tasks and clear the paused
        flag. Returns immediately when the run is already active."""
        self.log.info("Received resume command")
        if not self.paused:
            self.log.info("Service is not paused")
            return

        self.writer.running = True
        for reader in self.readers:
            reader.running = True

        self.writer_task = asyncio.create_task(self.writer.run())
        self.reader_tasks = []
        for reader in self.readers:
            self.reader_tasks.append(asyncio.create_task(reader.run()))
        self.log.info(f"All {len(self.reader_tasks) + 1} tasks reinitialized by resume")


        self.paused = False
        self.log.info("Service resumed")
    async def shutdown(self):
        """Stop ingesting and end the run loop."""
        self.log.info("Received shutdown command")
        await self.drain()
        self.running = False

    async def drain(self):
        """Stop the current generation of workers with both queues emptied.

        Stops the readers and awaits them to completion, each re-enqueueing
        the unfinished remainder of its range. Stops the writer only after
        that, so it commits everything the readers left. See ADR-0009 for
        the ordering requirement.

        Safe to call at any point, including before the workers exist, and
        returns immediately when every worker task has already finished.
        """
        if all([reader.done() for reader in self.reader_tasks]) and (self.writer_task is None or self.writer_task.done()):
            self.log.info("All workers are done")
            return
        
        self.log.info("Draining workers")
        for reader in self.readers:
            reader.running = False
        await asyncio.gather(*self.reader_tasks)
        self.log.info("Finished draining readers")
        self.writer.running = False
        if self.writer_task:
            await self.writer_task
        self.log.info("Finished draining writer")
