"""Progress: the core's stage records become per-source states on the GUI thread."""

from buysmarter.gui.progress import Progress
from buysmarter.services.collect import _safe, stage

_WAIT_MS = 3000


async def _stub_run() -> None:
    async def ok() -> list[str]:
        return []

    async def dead() -> None:
        raise ConnectionError("ozon down")

    await _safe("kaspi", ok())
    await _safe("ozon", dead())
    with stage("ranking"):
        pass


def test_stubbed_run_emits_running_then_done_or_failed_per_source(qtbot, runner):
    progress = Progress()
    seen: list[tuple[str, str, str]] = []
    progress.changed.connect(lambda *args: seen.append(args))
    progress.attach()
    try:
        job = runner.submit(_stub_run())
        with qtbot.waitSignal(job.finished, timeout=_WAIT_MS):
            pass
        qtbot.waitUntil(lambda: len(seen) == 6, timeout=_WAIT_MS)
    finally:
        progress.detach()
    assert seen == [
        ("kaspi", "running", "collect[kaspi]"),
        ("kaspi", "done", "collect[kaspi]"),
        ("ozon", "running", "collect[ozon]"),
        ("ozon", "failed", "collect[ozon]"),
        ("", "running", "ranking"),
        ("", "done", "ranking"),
    ]


def test_detached_progress_stops_emitting(qtbot, qapp):
    progress = Progress()
    seen = []
    progress.changed.connect(lambda *args: seen.append(args))
    progress.attach()
    progress.detach()
    with stage("ranking"):
        pass
    qapp.processEvents()
    assert seen == []
