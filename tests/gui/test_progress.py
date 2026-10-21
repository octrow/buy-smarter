"""Progress: a tracked job's stage records become per-source states on the GUI thread."""

import asyncio

from PySide6.QtCore import QObject

from buysmarter.gui.progress import Progress
from buysmarter.services.collect import _safe, stage
from tests.gui.conftest import WAIT_MS


async def _stub_run() -> None:
    async def ok() -> list[str]:
        return []

    async def dead() -> None:
        raise ConnectionError("ozon down")

    await _safe("kaspi", ok())
    await _safe("ozon", dead())
    with stage("ranking"):
        pass


def _recorder(progress: Progress) -> list[tuple[str, str, str]]:
    seen: list[tuple[str, str, str]] = []
    progress.changed.connect(lambda *args: seen.append(args))
    return seen


def test_stubbed_run_emits_running_then_done_or_failed_per_source(qtbot, runner):
    owner = QObject()  # the screen, in the app
    progress = Progress(owner)
    seen = _recorder(progress)
    job = runner.submit(progress.track(_stub_run()))
    with qtbot.waitSignal(job.finished, timeout=WAIT_MS):
        pass
    qtbot.waitUntil(lambda: len(seen) == 6, timeout=WAIT_MS)
    assert seen == [
        ("kaspi", "running", "collect[kaspi]"),
        ("kaspi", "done", "collect[kaspi]"),
        ("ozon", "running", "collect[ozon]"),
        ("ozon", "failed", "collect[ozon]"),
        ("", "running", "ranking"),
        ("", "done", "ranking"),
    ]


def test_two_jobs_at_once_each_see_only_their_own_stages(qtbot, runner):
    owner = QObject()
    search, refresh = Progress(owner), Progress(owner)
    seen_search, seen_refresh = _recorder(search), _recorder(refresh)

    async def stages(name: str) -> None:
        for _ in range(3):
            with stage(name):
                await asyncio.sleep(0.01)  # interleave with the other job

    done = []
    for progress, name in ((search, "search-stage"), (refresh, "refresh-stage")):
        runner.submit(progress.track(stages(name))).finished.connect(done.append)
    qtbot.waitUntil(lambda: len(done) == 2, timeout=WAIT_MS)
    qtbot.waitUntil(lambda: len(seen_search) == len(seen_refresh) == 6, timeout=WAIT_MS)
    assert {s for _, _, s in seen_search} == {"search-stage"}
    assert {s for _, _, s in seen_refresh} == {"refresh-stage"}


def test_untracked_work_emits_nothing(qtbot, qapp):
    owner = QObject()  # the screen, in the app
    progress = Progress(owner)
    seen = _recorder(progress)
    with stage("ranking"):
        pass
    qapp.processEvents()
    assert seen == []


def test_each_screen_owns_its_progress_so_it_is_never_freed_on_the_loop_thread(qtbot, runner):
    # The loop thread holds Progress.track's emitter in task contexts. Parentless, the Progress
    # could be freed there when its screen goes, and Qt crashes deleting a QObject off its thread.
    from buysmarter.gui.screens.history import HistoryScreen
    from buysmarter.gui.screens.search import SearchScreen

    for screen in (SearchScreen(runner), HistoryScreen(runner)):
        qtbot.addWidget(screen)
        assert screen._progress.parent() is screen
