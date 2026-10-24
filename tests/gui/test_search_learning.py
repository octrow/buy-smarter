"""Search screen: a new category is learned before its search (#26, ADR-0006)."""

import pytest

from buysmarter.gui import app as gui_app
from buysmarter.gui.learning import CategoryLearning
from buysmarter.gui.screens.search import SearchScreen
from buysmarter.services.auto_learn import NeedsLearning
from tests.gui.conftest import WAIT_MS
from tests.gui.test_app import _no_prefs

CATEGORY = "кофемолка"


def _new_category(result, calls):
    """The core stops while ``learn_first`` is on, as for a category never learned."""

    async def recommend(query, **kwargs):
        calls.append(kwargs["learn_first"])
        if kwargs["learn_first"]:
            raise NeedsLearning(CATEGORY)
        return result

    return recommend


@pytest.fixture
def calls() -> list[bool]:
    """``learn_first`` of each search the screen ran."""
    return []


@pytest.fixture
def waiting(qtbot, runner, result, fakes, counted, calls) -> SearchScreen:
    """A search of a new category, stopped and waiting for its learning."""
    screen = SearchScreen(
        runner, recommend=_new_category(result, calls), learning=CategoryLearning(runner)
    )
    qtbot.addWidget(screen)
    screen.query.setText(CATEGORY)
    screen.start()
    qtbot.waitUntil(lambda: fakes.learned == [CATEGORY], timeout=WAIT_MS)
    return screen


def test_the_search_waits_for_the_learning_then_runs_with_it(qtbot, waiting, fakes, counted, calls):
    screen = waiting

    assert "Изучаю категорию «кофемолка»" in screen.stage.text()
    assert not screen.skip_learning_button.isHidden()
    assert screen.cancel_button.isEnabled() and not screen.start_button.isEnabled()
    assert screen.is_running()
    assert counted == [1]
    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        fakes.release(CATEGORY)
    assert calls == [True, False]
    assert screen.skip_learning_button.isHidden()


def test_search_without_learning_runs_now_and_the_learning_goes_on(qtbot, waiting, fakes, calls):
    screen = waiting

    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        screen.skip_learning_button.click()

    assert calls == [True, False]
    assert screen.learning.running(CATEGORY)
    fakes.release(CATEGORY)
    qtbot.waitUntil(lambda: not screen.learning.running(CATEGORY), timeout=WAIT_MS)
    assert calls == [True, False]  # the learning's end does not search again


def test_cancel_stops_the_learning_and_the_search(qtbot, waiting, fakes, calls):
    screen = waiting

    screen.cancel()

    qtbot.waitUntil(lambda: not screen.learning.running(CATEGORY), timeout=WAIT_MS)
    qtbot.wait(100)
    assert calls == [True]
    assert "отменён" in screen.message.text()
    assert screen.start_button.isEnabled() and not screen.cancel_button.isEnabled()
    assert screen.skip_learning_button.isHidden()
    assert not screen.is_running()


def test_a_failed_learning_searches_without_it_and_says_so(qtbot, waiting, fakes, calls):
    fakes.outcome = False
    screen = waiting

    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        fakes.release(CATEGORY)

    assert calls == [True, False]
    assert "Не удалось изучить категорию" in screen.message.text()


def test_the_window_shares_one_learning_between_search_and_report(qtbot, runner, monkeypatch):
    monkeypatch.setattr(gui_app.prefs, "get_all", _no_prefs)
    window = gui_app.MainWindow(runner)
    qtbot.addWidget(window)

    assert window.search.learning is window.learning is window.report.learning
