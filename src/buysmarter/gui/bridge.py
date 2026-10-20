"""Run the async core from Qt: one asyncio loop in a worker thread, results back as Qt signals."""

import asyncio
import concurrent.futures
import itertools
import logging
import threading
from collections.abc import Coroutine
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot

logger = logging.getLogger(__name__)

USER_ERROR = "Something went wrong. The details are in the log file."
_SHUTDOWN_TIMEOUT_S = 10.0


class _LoopSide:
    """The part of a job the loop thread holds: never the ``Job`` QObject itself.

    Qt must delete a QObject on its own (GUI) thread; anything the loop thread references may
    lose its last reference there, so the loop side is a plain object.
    """

    def __init__(self) -> None:
        self.task: asyncio.Task[Any] | None = None  # set on the loop thread once it starts
        self.cancel_requested = False

    async def run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        self.task = asyncio.current_task()
        if self.cancel_requested:
            coro.close()
            raise asyncio.CancelledError
        try:
            return await coro
        finally:
            # A failed job's traceback holds this frame: without the task it is no reference
            # cycle, so the loop thread frees it by refcount, never in a GC pass that could
            # collect a QObject (a screen's Progress, in the frames below) on the wrong thread.
            self.task = None

    def cancel(self) -> None:
        if self.task is None:
            self.cancel_requested = True
        else:
            self.task.cancel()


class Job(QObject):
    """One submitted coroutine; exactly one of ``finished``, ``failed``, ``cancelled`` fires.

    The signals are emitted on the GUI thread, so any slot (a lambda too) may touch widgets.
    """

    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, side: _LoopSide, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._side = side
        self._loop = loop

    def cancel(self) -> None:
        """Cancel the coroutine; ``cancelled`` fires only after its ``finally`` blocks ran.

        The asyncio task is cancelled on the loop thread: cancelling the concurrent future
        instead would settle it at once, while the browser ``aclose`` is still running.
        """
        self._loop.call_soon_threadsafe(self._side.cancel)

    def _deliver(self, future: concurrent.futures.Future[Any]) -> None:
        if future.cancelled():
            self.cancelled.emit()
        elif (exc := future.exception()) is not None:
            logger.error("GUI job failed", exc_info=exc)
            self.failed.emit(USER_ERROR)
        else:
            self.finished.emit(future.result())


class CoreRunner(QObject):
    """Owns the core's event loop thread; ``submit`` coroutines, ``shutdown`` once on exit.

    Keep the runner alive until ``shutdown`` returns: the loop thread's done callbacks hold it.
    """

    _settled = Signal(int, object)  # (job id, future): loop thread -> GUI thread hop

    def __init__(self) -> None:
        super().__init__()
        self._loop = asyncio.new_event_loop()
        self._jobs: dict[int, Job] = {}
        self._ids = itertools.count()
        self._settled.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
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
        side = _LoopSide()
        job, job_id = Job(side, self._loop), next(self._ids)
        self._jobs[job_id] = job
        future = asyncio.run_coroutine_threadsafe(side.run(coro), self._loop)
        future.add_done_callback(lambda fut: self._settled.emit(job_id, fut))
        return job

    @Slot(int, object)
    def _deliver(self, job_id: int, future: concurrent.futures.Future[Any]) -> None:
        self._jobs.pop(job_id)._deliver(future)

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
