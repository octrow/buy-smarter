"""Search screen: form → stubbed core with live progress → report, cancel, prefill."""

import asyncio
import threading

from buysmarter.gui.screens.search import STATE_LABELS, SearchScreen
from buysmarter.services.collect import _safe
from tests.gui.conftest import WAIT_MS


def _stub_recommend(result, calls):
    async def recommend(query, **kwargs):
        calls.append((query, kwargs))

        async def ok():
            return []

        async def dead():
            raise ConnectionError("ozon down")

        await _safe("kaspi", ok())
        await _safe("ozon", dead())
        return result

    return recommend


def test_search_runs_with_progress_and_hands_the_result_to_the_report(qtbot, runner, result):
    calls = []
    screen = SearchScreen(runner, recommend=_stub_recommend(result, calls))
    qtbot.addWidget(screen)
    screen.query.setText("ирригатор до 40000 Алматы")
    screen.sources["olx"].setChecked(False)

    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS) as blocker:
        screen.start()
        assert not screen.start_button.isEnabled() and screen.cancel_button.isEnabled()

    got, run = blocker.args
    assert got is result
    assert dict(run.source_states) == {"kaspi": "done", "ozon": "failed", "olx": "skipped"}
    assert screen.status["ozon"].text() == STATE_LABELS["failed"]
    assert calls == [
        (
            "ирригатор до 40000 Алматы",
            {"city": None, "budget": None, "use_llm": True, "sources": ["kaspi", "ozon"]},
        )
    ]
    assert screen.start_button.isEnabled() and not screen.cancel_button.isEnabled()


def test_cancel_stops_the_search_shows_no_report_and_allows_a_new_one(qtbot, runner, result):
    started = threading.Event()

    async def slow(query, **kwargs):
        started.set()
        await asyncio.sleep(60)

    screen = SearchScreen(runner, recommend=slow)
    qtbot.addWidget(screen)
    reports = []
    screen.report_ready.connect(lambda *args: reports.append(args))
    screen.query.setText("ирригатор")
    screen.start()
    qtbot.waitUntil(started.is_set, timeout=WAIT_MS)
    screen.cancel()
    qtbot.waitUntil(screen.start_button.isEnabled, timeout=WAIT_MS)
    assert reports == []
    assert screen.message.text() == "Поиск отменён."

    screen._recommend = _stub_recommend(result, [])
    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        screen.start()


def test_nothing_to_recommend_is_shown_as_a_message(qtbot, runner):
    async def empty(query, **kwargs):
        raise ValueError("no candidates passed the filters")

    screen = SearchScreen(runner, recommend=empty)
    qtbot.addWidget(screen)
    screen.query.setText("ирригатор")
    screen.start()
    qtbot.waitUntil(screen.start_button.isEnabled, timeout=WAIT_MS)
    assert screen.message.text() == "No recommendation: no candidates passed the filters"


def test_empty_query_does_not_start(qtbot, runner):
    screen = SearchScreen(runner)
    qtbot.addWidget(screen)
    screen.start()
    assert screen.start_button.isEnabled()
    assert "Введите запрос" in screen.message.text()


def test_saved_preferences_prefill_the_form(qtbot, runner):
    screen = SearchScreen(runner)
    qtbot.addWidget(screen)
    screen.apply_prefs({"city": "Астана", "budget": "40 000", "marketplaces": "kaspi, olx"})
    assert screen.city.text() == "Астана"
    assert screen.budget.value() == 40000
    assert {n: b.isChecked() for n, b in screen.sources.items()} == {
        "kaspi": True,
        "ozon": False,
        "olx": True,
    }
