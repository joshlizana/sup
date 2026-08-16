"""Thin Jetstream websocket client: connect at a cursor, receive one
message at a time, close. Shared by the backfill shard workers, the live
tail, and the retention-floor probe.
"""

import orjson
import asyncio
import logging
import websockets
from sup.util import register, tid_us

class JetstreamClient:
    def __init__(self, url: str, cursor: int):
        self.cursor: int = cursor
        self.url: str = url
        self.uri: str = (f"{self.url}?cursor={self.cursor}"
        + "&wantedCollections=app.bsky.feed.post"
        + "&wantedCollections=app.bsky.feed.like"
        + "&wantedCollections=app.bsky.feed.repost"
        + "&wantedCollections=app.bsky.graph.follow"
        + "&wantedCollections=app.bsky.graph.block")
        self.ws: websockets.ClientConnection = None
        self.connected = False
        self.log = register(logging.getLogger(__name__), self.__class__.__name__)

    async def __aenter__(self):
        self.log.info(f"Opening connection to {self.url}")
        await self.connect()
        return self

    async def connect(self):
        """Open the websocket, retrying with exponential backoff.

        4 attempts, sleeping 1, 2, 4, then 8 seconds — roughly 15 seconds
        across the whole sequence. Raises once the attempts are spent.
        """
        retry = 0
        while not self.connected and retry < 4:
            try:
                self.ws = await websockets.connect(self.uri, close_timeout=1, ping_interval=None)
                self.connected = True
            except Exception as e:
                self.log.error(f"Error connecting to WebSocket: {e}")
                await asyncio.sleep(2 ** retry)  # Wait before retrying
                retry += 1

        if not self.connected:
            raise RuntimeError("Failed to connect to WebSocket")
        else:
            self.log.info(f"Successfully connected to {self.url}")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.log.info(f"Closing connection to {self.url}")
        if self.ws:
            await self.ws.close()
        self.connected = False

    async def recv(self):
        """Receive one message as raw JSON text."""
        return await self.ws.recv()

    async def get_endpoint_health(self):
        """Return `(retention, health)` for this endpoint.

        `retention` is the `time_us` of the oldest event served; `health`
        is 1 when that event's witness lag is within tolerance and 0
        otherwise, including when the message fails to parse. Reads until
        the first commit message, then closes. Requires the client to have
        been constructed with cursor 0.
        """
        found_retention = False
        while not found_retention:
            msg = await self.ws.recv()
            try:
                message = orjson.loads(msg)
                if message.get("kind") == "commit":
                    await self.ws.close()

                    found_retention = True
                    commit = message.get("commit")
                    rev = commit.get("rev")
                    time_us = message.get("time_us")
                    rev_us = tid_us(rev)
                    health = 1 if time_us - rev_us < 10_000_000 else 0
                    return (time_us, health)
            except Exception:
                await self.ws.close()
                raise