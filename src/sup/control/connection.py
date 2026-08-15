"""Control-plane transport: per-service Listener/Client pair over
multiprocessing.connection, guarded by a flock-based single-instance lock
and a shared, persisted authkey. See ADR-0007.

flock, socket bind and connect, send and recv all block the calling
thread, so each runs through `asyncio.to_thread`.
"""

import io
import os
import fcntl
import asyncio
import logging
from pathlib import Path
from sup.util import register
from sup.config import Config
from multiprocessing.connection import Listener, Client

class SupListener:
    """Owns one service's control-plane socket: the flock single-instance
    lock and the bound Listener. Acquiring the lock before binding means a
    second instance of the same service fails fast with a clear error."""

    def __init__(self, service: str):
        self._auth_key: bytes = None
        self.service: str = service
        self.control_path: Path = Config().control_path
        self.listener: Listener | None = None
        self.lock_file: Path = self.control_path / f"{self.service}.lock"
        self.port: Path = self.control_path / f"{self.service}.port"
        self.locked: bool = False
        self.file_description: io.TextIOWrapper | None = None
        self.log = register(logging.getLogger(__name__), self.__class__.__name__)

    async def __aenter__(self):
        """Take the lock, remove any socket left by an unclean exit, then
        bind. Holding the lock is what makes the removal safe."""
        self.log.info("Initializing control listener")
        if not self.locked:
            await asyncio.to_thread(self._get_lock)
        if not self.listener:
            if self.port.exists():
                self.port.unlink()
            if not self._auth_key:
                self._auth_key = bytes.fromhex(await get_auth_key())
            self.listener = await asyncio.to_thread(Listener, address=str(self.port), authkey=self._auth_key)
            # Bind gives the socket file umask-derived permissions, commonly
            # 0755; this narrows them to the owner.
            await asyncio.to_thread(os.chmod, self.port, 0o600)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close the socket and release the lock."""
        self.log.info("Exiting control listener")
        if self.listener:
            self.listener.close()
            if self.port.exists():
                self.port.unlink()
        if self.locked:
            await asyncio.to_thread(self._release_lock)

    def accept(self):
        """Block until a connection arrives and return it. Synchronous;
        `Controller._accept_loop` is the sole caller and already runs on
        its own thread."""
        if not self.listener:
            raise RuntimeError("Listener not initialized")
        return self.listener.accept()

    def _get_lock(self):
        # flock is advisory and whole-file, and releases on last-fd-close
        # or process exit, including kill -9. LOCK_NB raises
        # BlockingIOError when another instance holds it.
        self.file_description = open(self.lock_file, "w")
        try:
            fcntl.flock(self.file_description.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.locked = True
        except (BlockingIOError):
            self.file_description.close()
            self.log.info(f"Failed to acquire lock for {self.lock_file}. {self.service} service already running.")
            raise RuntimeError(f"{self.service} service is already running.")

    def _release_lock(self):
        fcntl.flock(self.file_description.fileno(), fcntl.LOCK_UN)
        self.file_description.close()
        self.locked = False

    

class SupClient:
    """Client side of the control-plane connection: connects to a running
    service's SupListener to deliver a command (pause/resume/shutdown), or
    to make the self-connect that wakes a blocked accept() during
    shutdown."""

    def __init__(self, service: str):
        self.service: str = service
        self._auth_key: bytes = None
        self.control_path: Path = Config().control_path
        self.port: Path = self.control_path / f"{self.service}.port"
        self.client: Client | None = None
        self.log = register(logging.getLogger(__name__), self.__class__.__name__)

    async def __aenter__(self):
        self.log.info("Initializing control client")
        if not self.client:
            if not self._auth_key:
                self._auth_key = bytes.fromhex(await get_auth_key())
            self.client = await asyncio.to_thread(Client,address=str(self.port), authkey=self._auth_key)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.log.info("Exiting control client")
        if self.client:
            self.client.close()

    async def send(self, msg):
        """Deliver one command dict, e.g. `{"cmd": "pause"}`."""
        if not self.client:
            self.log.error("Client not initialized")
            raise RuntimeError("Client not initialized")
        return await asyncio.to_thread(self.client.send, msg)

async def get_auth_key():
    """Read the shared control-plane authkey, generating it on first use.

    O_CREAT | O_WRONLY | O_EXCL makes creation atomic: when two processes
    race, one os.open() succeeds and the other raises FileExistsError,
    waits briefly, and reads the key the winner wrote.
    """
    logger = register(logging.getLogger(__name__), "get_auth_key")
    auth_path = Config().control_path / "key.auth"
    auth_key = None
    if not auth_path.exists():
        try:
            file = os.open(auth_path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
            with os.fdopen(file, "w") as f:
                f.write(os.urandom(32).hex())
        except FileExistsError:
            logger.error(f"Auth file already exists: {auth_path}")
            await asyncio.sleep(0.1)
    with open(auth_path, "r") as f:
        auth_key = f.read().strip()
    return auth_key