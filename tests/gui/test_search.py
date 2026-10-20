"""Search screen: form → stubbed core with live progress → report, cancel, prefill."""

import asyncio
import threading

from buysmarter.gui.bridge import USER_ERROR
from buysmarter.gui.screens.search import STATE_LABELS, SearchScreen
from buysmarter.ranking.models import Candidate, QueryConstraints
from buysmarter.services.collect import _safe
from buysmarter.services.recommend import NoSubstanceMatch
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


async def _ozon_hangs(started):
    from buysmarter.services.collect import stage

    async def ok():
        return []

    await _safe("kaspi", ok())
    with stage("collect[ozon]", source="ozon"):
        started.set()
        await asyncio.sleep(60)


def test_cancel_mid_ozon_leaves_no_marketplace_shown_as_running(qtbot, runner):
    started = threading.Event()

    async def hangs(query, **kwargs):
        await _ozon_hangs(started)

    screen = SearchScreen(runner, recommend=hangs)
    qtbot.addWidget(screen)
    screen.query.setText("ирригатор")
    screen.start()
    qtbot.waitUntil(started.is_set, timeout=WAIT_MS)
    running = STATE_LABELS["running"]
    qtbot.waitUntil(lambda: screen.status["ozon"].text() == running, timeout=WAIT_MS)
    screen.cancel()
    qtbot.waitUntil(screen.start_button.isEnabled, timeout=WAIT_MS)
    assert [screen.status[n].text() for n in ("kaspi", "ozon", "olx")] == [
        STATE_LABELS["done"],
        STATE_LABELS["skipped"],
        STATE_LABELS["skipped"],
    ]


def test_a_failing_search_shows_a_message_without_internals_and_stays_usable(qtbot, runner):
    async def boom(query, **kwargs):
        raise RuntimeError("db password=hunter2")

    screen = SearchScreen(runner, recommend=boom)
    qtbot.addWidget(screen)
    screen.query.setText("ирригатор")
    screen.start()
    qtbot.waitUntil(screen.start_button.isEnabled, timeout=WAIT_MS)
    assert screen.message.text() == USER_ERROR
    assert screen.status["kaspi"].text() == STATE_LABELS["skipped"]


def test_no_substance_match_shows_the_core_message(qtbot, runner):
    near = Candidate(source="kaspi", source_product_id="7", title="Effaclar Duo", price=9000)
    exc = NoSubstanceMatch(["adapalene"], 12, [near])

    async def none(query, **kwargs):
        raise exc

    screen = SearchScreen(runner, recommend=none)
    qtbot.addWidget(screen)
    screen.query.setText("адапален")
    screen.start()
    qtbot.waitUntil(screen.start_button.isEnabled, timeout=WAIT_MS)
    assert screen.message.text() == f"{exc.message()}\n• Effaclar Duo — 9,000 ₸ [kaspi]"


def test_form_values_and_saved_excluded_brands_reach_the_core(qtbot, runner, result):
    calls, parsed_for = [], []

    async def parse(query, use_llm):
        parsed_for.append((query, use_llm))
        return QueryConstraints(category="ирригатор", excluded_brands=["Noname"])

    screen = SearchScreen(runner, recommend=_stub_recommend(result, calls), parse=parse)
    qtbot.addWidget(screen)
    screen.apply_prefs({"excluded_brands": ["Generic", "Noname"]})
    screen.query.setText("ирригатор")
    screen.city.setText("Астана")
    screen.budget.setValue(30000)
    screen.use_llm.setChecked(False)
    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        screen.start()
    ((_query, kwargs),) = calls
    assert parsed_for == [("ирригатор", False)]
    assert kwargs["parsed"].excluded_brands == ["Noname", "Generic"]
    assert {k: kwargs[k] for k in ("city", "budget", "use_llm")} == {
        "city": "Астана",
        "budget": 30000,
        "use_llm": False,
    }


def test_unknown_stored_marketplaces_leave_the_boxes_ticked(qtbot, runner):
    screen = SearchScreen(runner)
    qtbot.addWidget(screen)
    screen.apply_prefs({"marketplaces": "wildberries"})
    assert all(box.isChecked() for box in screen.sources.values())


def test_a_typed_city_is_not_overwritten_by_preferences_arriving_later(qtbot, runner):
    screen = SearchScreen(runner)
    qtbot.addWidget(screen)
    screen.city.setText("Караганда")
    screen.city.setModified(True)  # what typing into the field sets
    screen.apply_prefs({"city": "Астана"})
    assert screen.city.text() == "Караганда"


def test_preferences_arriving_after_the_checks_keep_an_unavailable_source_unticked(qtbot, runner):
    screen = SearchScreen(runner)
    qtbot.addWidget(screen)
    screen.set_unavailable({"ozon": "no browser"})
    screen.apply_prefs({"marketplaces": ["kaspi", "ozon"]})
    assert not screen.sources["ozon"].isChecked()
