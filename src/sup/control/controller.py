import signal
import asyncio
import logging
from typing import Literal
from sup.util import register
from sup.control.connection import SupListener, SupClient


class Controller:
    """Generic, per-service control-plane object: owns a SupListener's
    accept loop and dispatches pause/resume/shutdown commands received over
    it.

    Three hooks are injected, each supplying service-specific behavior:

    - quiesce: stop doing work.
    - resume: start doing work again after a pause.
    - shutdown: quiesce, then end the service's own run loop.

    Pause keeps the accept loop, lock, and socket alive; shutdown stops
    accepting."""

    def __init__(self, service: str, quiesce: callable, resume: callable, shutdown: callable):
        self.service: str = service
        self.client: SupClient | None = None
        self.listener: SupListener | None = None
        self.running: bool = False
        self._stopping: bool = False
        self.accept_task: asyncio.Task | None = None
        self.status: Literal["running", 
                             "stopped", 
                             "starting", 
                             "stopping", 
                             "pausing", 
                             "paused", 
                             "resuming"] = "starting"
        self.quiesce: callable = quiesce
        self.resume: callable = resume
        self.shutdown: callable = shutdown
        self.log = register(logging.getLogger(__name__), self.__class__.__name__)
        self._prev_handlers: dict = {}
        self._signal_task: asyncio.Task | None = None

    async def __aenter__(self):
        self.log.info(f"Attempting to acquire listener for {self.service}")
        self.listener = await SupListener(self.service).__aenter__()
        loop = asyncio.get_running_loop()

        def _on_signal(signum, frame):
            def _spawn():
                self._signal_task = asyncio.create_task(self._shutdown())
            try:
                loop.call_soon_threadsafe(_spawn)
            except RuntimeError:
                pass

        for sig in (signal.SIGTERM, signal.SIGINT):
            self._prev_handlers[sig] = signal.signal(sig, _on_signal)

        await self.start()
        self.running = True
        self.status = "running"
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # _shutdown() clears self.running first, so the accept loop's
        # recheck sees False on the connection _release_accept_block()
        # hands it and exits.
        try:
            await self._shutdown()
            await self._release_accept_block()
            await asyncio.gather(self.accept_task)


            self.log.info(f"Releasing listener for {self.service}")
            if self.listener:
                await self.listener.__aexit__(exc_type, exc_val, exc_tb)
            self.status = "stopped"
        finally:
            for sig, prev in self._prev_handlers.items():
                signal.signal(sig, prev)

    async def start(self):
        """Launch the accept loop on a worker thread, handing it the
        running event loop."""
        loop = asyncio.get_event_loop()
        self.accept_task = asyncio.create_task(asyncio.to_thread(self._accept_loop, loop))

    def _accept_loop(self, loop: asyncio.AbstractEventLoop):
        # Runs on its own thread: Listener.accept() blocks until a
        # connection arrives, and only a connection releases it.
        # run_coroutine_threadsafe hands each connection's handler back to
        # the event loop that owns the service.
        while self.running:
            try:
                conn = self.listener.accept()
                asyncio.run_coroutine_threadsafe(self._handle_connection(conn), loop)
            except Exception as e:
                self.log.error(f"Error occurred while accepting connection: {e}")
                break

    async def _handle_connection(self, conn):
        """Read commands from one client connection and dispatch them,
        replying with the resulting status once each hook completes."""
        try:
            while self.running:
                if await asyncio.to_thread(conn.poll, 0.1):
                    msg = await asyncio.to_thread(conn.recv)

                    match msg["cmd"]:
                        case "pause":
                            response = await self._pause()
                            await asyncio.to_thread(conn.send,response)
                        case "resume":
                            response = await self._resume()
                            await asyncio.to_thread(conn.send,response)
                        case "shutdown":
                            response = await self._shutdown()
                            await asyncio.to_thread(conn.send,response)
                        case "stopping":
                            # The self-connect from _release_accept_block()
                            # lands here, as a way to unblock accept().
                            pass
                        case _:
                            self.log.warning(f"Unhandled command: {msg['cmd']}")
        except Exception as e:
            self.log.error(f"Error occurred while handling connection: {e}")

    async def _shutdown(self):
        # Clearing self.running retires the accept loop and any live
        # connection handlers, so this instance stops taking commands.
        # _stopping latches here and is rechecked by _pause and _resume
        # after their own awaits, so a command already in flight leaves
        # this status in place.
        if self._stopping:
            return self.status
        
        self.status = "stopping"
        self._stopping = True
        await self.shutdown()

        self.running = False
        return self.status

    async def _pause(self):
        # self.running stays True, so the listener keeps accepting and a
        # paused instance holds its lock and socket.
        if self._stopping:
            return self.status
        
        self.status = "pausing"
        await self.quiesce()

        if not self._stopping:
            self.status = "paused"
        return self.status

    async def _resume(self):
        if self._stopping:
            return self.status
        
        self.status = "resuming"
        await self.resume()

        if not self._stopping:
            self.status = "running"
        return self.status

    async def _release_accept_block(self):
        """Unblock a thread waiting inside Listener.accept() by connecting
        to this service's own listener and sending a sentinel that
        _handle_connection ignores."""
        try:
            async with asyncio.timeout(15):
                async with SupClient(self.service) as client:
                    await client.send({"cmd": "stopping"})
        except (BrokenPipeError, ConnectionResetError, EOFError, asyncio.TimeoutError):
            pass
