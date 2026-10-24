"""Search screen: form → stubbed core with live progress → report, cancel, prefill."""

import asyncio
import threading

from buysmarter.gui.bridge import USER_ERROR
from buysmarter.gui.screens.search import STATE_LABELS, SearchScreen
from buysmarter.i18n import STAGE_LABELS, stage_label
from buysmarter.ranking.models import Candidate, QueryConstraints
from buysmarter.services.collect import _safe
from buysmarter.services.matching import UsedListing
from buysmarter.services.recommend import NoNewPick, NoSubstanceMatch
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
            {
                "city": None,
                "budget": None,
                "use_llm": True,
                "sources": ["kaspi", "ozon"],
                "learn_first": True,
            },
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
    assert (
        screen.message.text()
        == "Не нашлось подходящих предложений: no candidates passed the filters"
    )


def test_empty_query_does_not_start(qtbot, runner):
    screen = SearchScreen(runner)
    qtbot.addWidget(screen)
    screen.start()
    assert screen.start_button.isEnabled()
    assert "Введите запрос" in screen.message.text()


def test_saved_city_and_budget_are_hints_so_the_query_still_wins(qtbot, runner, result):
    # as the CLI: field > query > saved; the core falls back to the saved ones itself
    calls = []
    screen = SearchScreen(runner, recommend=_stub_recommend(result, calls))
    qtbot.addWidget(screen)
    screen.apply_prefs({"city": "Астана", "budget": "40 000", "marketplaces": "kaspi, olx"})
    assert screen.city.text() == "" and "Астана" in screen.city.placeholderText()
    assert screen.budget.value() == 0 and "40\xa0000" in screen.budget.specialValueText()
    assert screen.budget.property("state") == "hint"  # muted, as the city's placeholder

    screen.query.setText("ирригатор до 30000 Караганда")
    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        screen.start()
    assert calls[0][1]["city"] is None and calls[0][1]["budget"] is None
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
    assert screen.message.text() == f"{exc.message()}\n• Effaclar Duo — 9\xa0000 ₸ [kaspi]"


def test_nothing_new_in_budget_lists_the_olx_ads_instead(qtbot, runner):
    ads = [
        UsedListing(title="iPhone 13 128GB", price=180_000, new_price=373_000, saving=193_000),
        UsedListing(title="iPhone 13 mini", price=150_000, city="Алматы", standalone=True),
    ]

    async def none(query, **kwargs):
        raise NoNewPick({"price exceeds budget": 2}, ads)

    screen = SearchScreen(runner, recommend=none)
    qtbot.addWidget(screen)
    screen.query.setText("iphone 13 б/у до 250000")
    screen.start()
    qtbot.waitUntil(screen.start_button.isEnabled, timeout=WAIT_MS)
    assert screen.message.text().splitlines() == [
        "Новых предложений нет: дороже бюджета ×2. На OLX нашлось:",
        "• iPhone 13 128GB — 180\xa0000 ₸, на 193\xa0000 ₸ дешевле нового",
        "• iPhone 13 mini — 150\xa0000 ₸ (Алматы)",
    ]


def test_form_values_and_saved_excluded_brands_reach_the_core(qtbot, runner, result):
    calls, parsed_for = [], []

    async def parse(query, use_llm):
        parsed_for.append((query, use_llm))
        return QueryConstraints(search_phrase="ирригатор", excluded_brands=["Noname"])

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


def test_core_stage_names_read_in_russian_and_an_unknown_one_reads_generic():
    assert stage_label("query parse") == STAGE_LABELS["query parse"]
    assert stage_label("collect[ozon]") == STAGE_LABELS["collect"]
    assert stage_label("something new") == "Работаю"


def test_a_running_search_shows_it_is_busy_and_how_long_it_has_run(qtbot, runner):
    started = threading.Event()

    async def hangs(query, **kwargs):
        await _ozon_hangs(started)

    screen = SearchScreen(runner, recommend=hangs)
    qtbot.addWidget(screen)
    screen.query.setText("ирригатор")
    screen.start()
    qtbot.waitUntil(started.is_set, timeout=WAIT_MS)
    want = f"{STAGE_LABELS['collect']}… "
    qtbot.waitUntil(lambda: screen.stage.text().startswith(want), timeout=WAIT_MS)
    assert screen.stage.text().endswith(" с")
    assert screen.busy.isVisibleTo(screen) and screen.start_button.text() == "Ищу…"

    screen.cancel()
    qtbot.waitUntil(screen.start_button.isEnabled, timeout=WAIT_MS)
    assert not screen.busy.isVisibleTo(screen) and screen.stage.text() == ""
    assert screen.start_button.text() == "Найти"


def test_health_checks_landing_mid_search_keep_the_marketplace_statuses(qtbot, runner):
    started = threading.Event()

    async def hangs(query, **kwargs):
        await _ozon_hangs(started)

    screen = SearchScreen(runner, recommend=hangs)
    qtbot.addWidget(screen)
    screen.query.setText("ирригатор")
    screen.start()
    qtbot.waitUntil(started.is_set, timeout=WAIT_MS)
    screen.set_unavailable({})
    assert screen.status["olx"].text() == STATE_LABELS["waiting"]
    screen.cancel()
    qtbot.waitUntil(screen.start_button.isEnabled, timeout=WAIT_MS)


def test_a_finished_search_shows_what_each_marketplace_brought(qtbot, runner, result):
    screen = SearchScreen(runner, recommend=_stub_recommend(result, []))
    qtbot.addWidget(screen)
    screen.query.setText("ирригатор")

    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        screen.start()

    assert screen.status["kaspi"].text() == f"{STATE_LABELS['done']} · 2"
    assert screen.message.text() == "Найдено предложений: 3, б/у на OLX: 1."
    assert not screen.report_button.isHidden()


def test_a_marketplace_a_capability_opened_shows_its_progress(qtbot, runner, result):
    """A used query brings in OLX though the form left it unticked: its state must follow."""

    async def recommend(query, **kwargs):
        async def ok():
            return []

        await _safe("kaspi", ok())
        await _safe("olx", ok())
        return result

    screen = SearchScreen(runner, recommend=recommend)
    qtbot.addWidget(screen)
    screen.sources["olx"].setChecked(False)
    screen.query.setText("б/у ирригатор")

    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        screen.start()

    assert screen.status["olx"].text().startswith(STATE_LABELS["done"])


def _reports(*names):
    from pathlib import Path

    return lambda: [Path(f"/r/{n}.md") for n in names]


def test_recent_searches_show_the_latest_distinct_queries(qtbot, runner):
    screen = SearchScreen(
        runner,
        list_reports=_reports(
            "20261022-023500-мужские-кроссовки",
            "20261022-023200-мужские-кроссовки",
            "20261021-203800-смартфон-до-200000",
            "20261019-184300-гитара-до-30000",
            "20261013-172500-iphone-13",
        ),
    )
    qtbot.addWidget(screen)
    screen.refresh_recent()
    assert screen.recent_queries() == ["мужские кроссовки", "смартфон до 200000", "гитара до 30000"]
    assert screen.recent_box.isVisibleTo(screen)


def test_no_saved_searches_hides_the_recent_block(qtbot, runner):
    screen = SearchScreen(runner, list_reports=_reports())
    qtbot.addWidget(screen)
    screen.refresh_recent()
    assert screen.recent_queries() == []
    assert not screen.recent_box.isVisibleTo(screen)


def test_repeat_fills_the_query_and_starts_the_search(qtbot, runner, result):
    calls = []
    screen = SearchScreen(
        runner,
        recommend=_stub_recommend(result, calls),
        list_reports=_reports("20261021-203800-смартфон-до-200000"),
    )
    qtbot.addWidget(screen)
    screen.refresh_recent()
    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        screen.repeat_buttons()[0].click()
    assert screen.query.text() == "смартфон до 200000"
    assert calls[0][0] == "смартфон до 200000"
