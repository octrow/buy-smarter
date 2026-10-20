"""Run the async core from Qt: one asyncio loop in a worker thread, results back as Qt signals."""

import asyncio
import concurrent.futures
import logging
import threading
import weakref
from collections.abc import Coroutine
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot

logger = logging.getLogger(__name__)

USER_ERROR = "Something went wrong. The details are in the log file."
_SHUTDOWN_TIMEOUT_S = 10.0


class Job(QObject):
    """One submitted coroutine; exactly one of ``finished``, ``failed``, ``cancelled`` fires.

    The signals are emitted on the GUI thread, so any slot (a lambda too) may touch widgets.
    """

    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    _settled = Signal(object)  # loop thread -> GUI thread hop

    def __init__(self, coro: Coroutine[Any, Any, Any], loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._loop = loop
        self._task: asyncio.Task[Any] | None = None  # set on the loop thread once it starts
        self._cancel_requested = False
        self._settled.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        future = asyncio.run_coroutine_threadsafe(self._run(coro), loop)
        # A weak reference: the loop thread must never drop the last reference to this QObject
        # (Qt deletes it on the wrong thread). CoreRunner holds the job until it is delivered.
        ref = weakref.ref(self)

        def settled(fut: concurrent.futures.Future[Any]) -> None:
            if (job := ref()) is not None:
                job._settled.emit(fut)

        future.add_done_callback(settled)

    async def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        self._task = asyncio.current_task()
        if self._cancel_requested:
            coro.close()
            raise asyncio.CancelledError
        return await coro

    def cancel(self) -> None:
        """Cancel the coroutine; ``cancelled`` fires only after its ``finally`` blocks ran.

        The asyncio task is cancelled on the loop thread: cancelling the concurrent future
        instead would settle it at once, while the browser ``aclose`` is still running.
        """
        self._loop.call_soon_threadsafe(self._cancel_in_loop)

    def _cancel_in_loop(self) -> None:
        if self._task is None:
            self._cancel_requested = True
        else:
            self._task.cancel()

    @Slot(object)
    def _deliver(self, future: concurrent.futures.Future[Any]) -> None:
        if future.cancelled():
            self.cancelled.emit()
        elif (exc := future.exception()) is not None:
            logger.error("GUI job failed", exc_info=exc)
            self.failed.emit(USER_ERROR)
        else:
            self.finished.emit(future.result())


class CoreRunner(QObject):
    """Owns the core's event loop thread; ``submit`` coroutines, ``shutdown`` once on exit."""

    def __init__(self) -> None:
        super().__init__()
        self._loop = asyncio.new_event_loop()
        self._jobs: set[Job] = set()
        self._thread = threading.Thread(target=self._run_loop, name="core-loop", daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        self._loop.run_until_complete(self._loop.shutdown_asyncgens())
        self._loop.close()

    def submit(self, coro: Coroutine[Any, Any, Any]) -> Job:
        """Schedule ``coro`` on the core loop; the runner keeps the job alive until it settles."""
        if not self._thread.is_alive():
            coro.close()
            raise RuntimeError("CoreRunner is shut down")
        job = Job(coro, self._loop)
        self._jobs.add(job)
        for signal in (job.finished, job.failed, job.cancelled):
            signal.connect(lambda *_: self._jobs.discard(job))
        return job

    def shutdown(self) -> None:
        """Cancel what still runs, let it clean up, then stop the loop and join its thread."""
        if not self._thread.is_alive():
            return
        drain = asyncio.run_coroutine_threadsafe(_cancel_all(), self._loop)
        try:
            drain.result(_SHUTDOWN_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            logger.warning("Core jobs did not stop within %ss; exiting anyway", _SHUTDOWN_TIMEOUT_S)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(_SHUTDOWN_TIMEOUT_S)


async def _cancel_all() -> None:
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
