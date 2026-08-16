"""Range-based ingest worker.

Claims a `(start, end)` `time_us` range from the shared work queue, reads
that range forward from a single Jetstream endpoint, and routes each
message to the output queue or the DLQ. The live tail is this same worker
holding a range that ends at `sys.maxsize`.

`Ingester` constructs one Reader per endpoint and drives `run()` as a
task. `running` defaults to True. Setting it False ends the run loop;
starting a fresh `run()` task resumes work.
"""

import sys
import time
import orjson
import asyncio
import logging
from collections import deque
from sup.config import Config
from sup.util import register, displayTime, tid_us
from sup.services.ingest.jetstream import JetstreamClient


class Reader:
    def __init__(self, endpoint: tuple[str, int, int], backfill_queue: asyncio.PriorityQueue, output_queue: asyncio.Queue, dlq_queue: asyncio.Queue):
        self.endpoint: tuple[str, int, int] = endpoint
        self.backfill_queue: asyncio.PriorityQueue = backfill_queue
        self.output_queue: asyncio.Queue = output_queue
        self.dlq_queue: asyncio.Queue = dlq_queue
        self.running: bool = True
        self.reading: bool = False
        self.latest_message_count: deque = deque([])
        self.throughput: int = 0
        self.current_cursor: int | None = None
        self.start_cursor: int | None = None
        self.end_cursor: int | None = None
        self.retention: int | None = None
        self.endpoint_health: int | None = None
        self.log = register(logging.getLogger(__name__), self.__class__.__name__)

    async def __aenter__(self):
        self.log.info("Initializing reader")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.log.info("Exiting reader")
        self.running = False

    async def run(self):
        """Claim ranges and read them until stopped.

        Each pass claims one range, reads it, and returns any unread
        remainder to the queue.
        """
        self.log.info(f"Reading on {self.endpoint[0]}")
        while self.running:
            if self.start_cursor is None and self.end_cursor is None:
                try:
                    await self.get_work()
                except Exception as e:
                    self.log.error(f"Error occurred while fetching work: {e}")

            # One connection per range, opened at current_cursor and reused
            # across every recv() in it.
            if self.current_cursor is not None and self.start_cursor is not None and self.end_cursor is not None:
                async with JetstreamClient(self.endpoint[0], self.current_cursor) as client:
                    while self.running and self.current_cursor < self.end_cursor:
                        self.reading = True
                        try:
                            data = await client.recv()
                            response = await self.process_message(data)
                            await self.update_throughput()
                            if response == "break":
                                break
                        except Exception as e:
                            self.log.error(f"Error occurred while reading from Jetstream: {e}")
                            break
                self.reading = False
            if self.current_cursor is not None and self.end_cursor is not None and self.current_cursor < self.end_cursor:
                await self.backfill_queue.put((self.current_cursor, self.end_cursor))
                self.log.info(f"Re-queued range: {displayTime(self.current_cursor)} to {displayTime(self.end_cursor)}")
                await asyncio.sleep(1)

            await self.update_throughput()
            self.start_cursor = None
            self.end_cursor = None

        self.log.info(f"Finished reading on {self.endpoint[0]}")


    async def get_work(self):
        """Claim the oldest eligible range from the shared queue.

        Establishes this endpoint's retention floor and health on the
        first call, returning without claiming when the probe fails so
        the next pass retries it. A range is ineligible when it starts
        below the retention floor, when it is the open-ended live tail
        and this endpoint is backfill-only, or when the endpoint is
        unhealthy; ineligible ranges go back to the queue for another
        Reader. Sets the cursor triple when a range is claimed, and
        sleeps a second before returning either way, pacing both an
        empty queue and a range this endpoint cannot make progress on.
        """
        # Probed once per Reader lifetime, then cached.
        async with asyncio.timeout(15):
            if self.running and (self.retention is None or self.endpoint_health is None):
                try:
                    async with JetstreamClient(self.endpoint[0], 0) as client:
                        self.retention, self.endpoint_health = await client.get_endpoint_health()
                    self.log.info(f"Retention: {displayTime(self.retention)}")
                except Exception as e:
                    self.log.error(f"Error occurred while fetching endpoint retention: {e}")
                    await asyncio.sleep(1)
                    return

        # Ranges below this endpoint's retention floor go back into the
        # shared queue for another Reader to claim.
        ineligible_work = []
        while self.running and self.backfill_queue.qsize() > 0 and self.start_cursor is None and self.end_cursor is None:
            work = await self.backfill_queue.get()
            start, end = work
            if start < self.retention or (self.endpoint[1] == 1 and end == sys.maxsize) or self.endpoint_health == 0:
                ineligible_work.append(work)
            else:
                self.current_cursor = start
                self.start_cursor = start
                self.end_cursor = end
                self.log.info(f"Claimed range: {displayTime(self.start_cursor)} to {displayTime(self.end_cursor)}")
        for work in ineligible_work:
            await self.backfill_queue.put(work)
        if self.start_cursor is None:
            await asyncio.sleep(1)

    async def process_message(self, data):
        """Queue one raw message for the writer, or send it to the DLQ.

        `did`, `time_us` and `kind` are top-level fields; `rkey` and `rev`
        are nested inside `commit`. The payload is queued verbatim
        alongside the extracted columns. Both queues are bounded, so a put
        against a full queue suspends until the writer drains it.

        A message missing any of the four columns is dropped, carrying no
        record from a wanted collection; the cursor still advances past it
        (TDD-0003 §4).

        `tid_us` falls back to `time_us` when `rev` decodes implausibly, so
        the stored column never carries the zero `sup.util.tid_us` returns
        (ADR-0018).

        Returns `"break"` for either edge of the claimed range, so `run()`
        ends the connection (ADR-0020). Past `end_cursor` the range is
        finished, and the cursor moves there so nothing is re-queued.
        Below `start_cursor` the endpoint has answered from its replay
        floor rather than the cursor asked for; the cursor holds, so the
        range returns to the queue for an endpoint that can serve it.
        """
        try:
            message = orjson.loads(data)
            # None for non-commit kinds; the kind check short-circuits
            # before anything reads through it.
            commit = message.get("commit")
            time_us = message.get("time_us")

            if time_us is not None and time_us > self.end_cursor:
                self.current_cursor = time_us
                return "break"
            if time_us is not None and time_us < self.start_cursor:
                return "break"

            
            if message.get("kind") == "commit" and message.get("did") and commit.get("rkey") and commit.get("rev") and message.get("time_us"):
                event_us = tid_us(commit.get("rev"))
                event_us = event_us if event_us != 0 else time_us
                await self.output_queue.put((int(time.time()), message.get("did"), commit.get("rkey"), commit.get("rev"), message.get("time_us"), self.endpoint[0], event_us, data))

            # The cursor advances for every kind of message, and holds its
            # previous value when time_us is absent.
            if time_us is not None and self.current_cursor < time_us:
                self.current_cursor = time_us
        except Exception as e:
            await self.dlq_queue.put((int(time.time()), str(e), data))

        self.latest_message_count.append(time.time())

    async def update_throughput(self):
        """Recompute `throughput` as messages/sec over the trailing 10
        seconds, evicting expired timestamps from the window."""
        if not self.latest_message_count:
            self.throughput = 0
        else:
            while self.latest_message_count and self.latest_message_count[0] < time.time() - 10:
                self.latest_message_count.popleft()
            self.throughput = len(self.latest_message_count) // 10