"""CoreRunner: the async core runs off the GUI thread and reports back on it."""

import asyncio

import pytest
from PySide6.QtCore import QThread

from buysmarter.gui.bridge import USER_ERROR, CoreRunner

_WAIT_MS = 3000


@pytest.fixture
def runner(qapp):
    r = CoreRunner()
    yield r
    r.shutdown()


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


def test_cancel_stops_a_sleeping_coroutine_and_runs_its_cleanup(qtbot, runner):
    started, cleaned = asyncio.Event(), []

    async def sleeper() -> None:
        try:
            started.set()
            await asyncio.sleep(60)
        finally:
            cleaned.append(True)

    job = runner.submit(sleeper())
    qtbot.waitUntil(started.is_set, timeout=_WAIT_MS)
    with qtbot.waitSignal(job.cancelled, timeout=_WAIT_MS):
        job.cancel()
    qtbot.waitUntil(lambda: cleaned == [True], timeout=_WAIT_MS)


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
