"""CoreRunner: the async core runs off the GUI thread and reports back on it."""

import asyncio
import gc
import threading
import weakref

import pytest
from PySide6.QtCore import QThread

from buysmarter.gui import bridge
from buysmarter.gui.bridge import USER_ERROR, CoreRunner

_WAIT_MS = 3000


async def _answer() -> int:
    await asyncio.sleep(0)
    return 42


def test_result_arrives_on_the_gui_thread(qtbot, qapp, runner):
    seen = []
    job = runner.submit(_answer())
    job.finished.connect(lambda value: seen.append((value, QThread.currentThread())))
    with qtbot.waitSignal(job.finished, timeout=_WAIT_MS):
        pass
    assert seen == [(42, qapp.thread())]


def test_exception_becomes_failed_with_a_message_free_of_internals(qtbot, runner, caplog):
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    job = runner.submit(boom())
    with qtbot.waitSignal(job.failed, timeout=_WAIT_MS) as blocker:
        pass
    assert blocker.args == [USER_ERROR]
    logged = [str(r.exc_info[1]) for r in caplog.records if r.exc_info]
    assert logged == ["secret internal detail"]


def test_a_failed_job_frees_its_frames_without_a_gc_pass(qtbot, runner, monkeypatch):
    monkeypatch.setattr(bridge.logger, "disabled", True)  # pytest keeps logged tracebacks

    class Held:  # stands in for a screen's Progress in the failing coroutine's frames
        pass

    held = Held()
    alive = weakref.ref(held)

    async def boom(obj: Held) -> None:
        raise RuntimeError(type(obj).__name__)

    gc.disable()
    try:
        job = runner.submit(boom(held))
        del held
        with qtbot.waitSignal(job.failed, timeout=_WAIT_MS):
            pass
        del job
        qtbot.waitUntil(lambda: alive() is None, timeout=_WAIT_MS)
    finally:
        gc.enable()


def test_cancel_fires_cancelled_only_after_the_cleanup_ran(qtbot, runner):
    started, cleaned, at_cancelled = threading.Event(), [], []

    async def sleeper() -> None:
        try:
            started.set()
            await asyncio.sleep(60)
        finally:
            await asyncio.sleep(0.05)  # an aclose() that takes a moment
            cleaned.append(True)

    job = runner.submit(sleeper())
    job.cancelled.connect(lambda: at_cancelled.append(list(cleaned)))
    qtbot.waitUntil(started.is_set, timeout=_WAIT_MS)
    with qtbot.waitSignal(job.cancelled, timeout=_WAIT_MS):
        job.cancel()
    assert at_cancelled == [[True]]


def test_cancel_before_the_coroutine_starts_settles_cancelled_without_running_it(qtbot, runner):
    hogging, release, ran = threading.Event(), threading.Event(), []

    async def hog_the_loop() -> None:
        hogging.set()
        release.wait(_WAIT_MS / 1000)  # blocks the loop thread, so `never` cannot start yet

    async def never() -> None:
        ran.append(True)

    runner.submit(hog_the_loop())
    hogging.wait(_WAIT_MS / 1000)  # `never` must be queued while the loop is busy
    job = runner.submit(never())
    job.cancel()
    release.set()
    with qtbot.waitSignal(job.cancelled, timeout=_WAIT_MS):
        pass
    assert ran == []


def test_submit_after_shutdown_refuses_and_closes_the_coroutine(qapp):
    runner = CoreRunner()
    runner.shutdown()
    coro = _answer()
    with pytest.raises(RuntimeError):
        runner.submit(coro)
    assert coro.cr_frame is None  # closed, so no "never awaited" warning


def test_shutdown_cancels_running_work_and_joins_the_thread(qapp):
    runner = CoreRunner()
    cleaned = []

    async def sleeper() -> None:
        try:
            await asyncio.sleep(60)
        finally:
            cleaned.append(True)

    runner.submit(sleeper())
    runner.shutdown()
    assert not runner._thread.is_alive()
    assert cleaned == [True]
    runner.shutdown()  # idempotent
