"""Learning on the report screen: per category, a new report keeps the other's learning.

The search starts a new category's learning (#26); the report only shows it and offers the
manual button.
"""

from buysmarter.gui import app as gui_app
from buysmarter.gui import learning as gui_learning
from buysmarter.gui.learning import CategoryLearning
from buysmarter.gui.screens import report as report_screen
from buysmarter.gui.screens.report import ReportScreen
from buysmarter.gui.screens.search import SearchRun
from tests.gui.test_app import _no_prefs

_RUN = SearchRun("ирригатор", True, {"kaspi": "done"})
_WAIT_MS = 3000
_RERUN = "Повторить поиск"


def _missing(result, category):
    constraints = result.constraints.model_copy(
        update={"product_type": category, "intent": "product"}
    )
    return result.model_copy(update={"knowledge_missing": True, "constraints": constraints})


def _screen(qtbot, runner):
    screen = ReportScreen(runner, learning=CategoryLearning(runner))
    qtbot.addWidget(screen)
    return screen


def _status(screen) -> str:
    return screen.learn_status.text()


def _learning_on_screen(qtbot, runner, result, fakes, category="ирригатор"):
    """The report of a category whose learning the search started."""
    screen = _screen(qtbot, runner)
    screen.learning.learn_now(category)
    screen.show_result(_missing(result, category), _RUN)
    qtbot.waitUntil(lambda: fakes.learned == [category], timeout=_WAIT_MS)
    return screen


def test_a_report_shows_the_learning_the_search_started(qtbot, runner, result, fakes, counted):
    screen = _learning_on_screen(qtbot, runner, result, fakes)
    qtbot.waitUntil(lambda: "Изучаю категорию" in _status(screen), timeout=_WAIT_MS)
    assert counted == [1]
    assert not screen.learn_button.isEnabled()
    fakes.release("ирригатор")


def test_a_report_starts_no_learning_on_its_own(qtbot, runner, result, fakes, counted):
    screen = _screen(qtbot, runner)
    screen.show_result(_missing(result, "ирригатор"), _RUN)
    qtbot.wait(100)
    assert fakes.learned == []
    assert counted == []
    assert screen.learn_button.isEnabled()  # the manual button stays


def test_the_learn_button_is_off_when_an_llm_step_was_skipped(qtbot, runner, result):
    skipped = _missing(result, "ирригатор").model_copy(update={"skipped_llm_steps": ["review"]})
    screen = _screen(qtbot, runner)
    screen.show_result(skipped, _RUN)
    assert not screen.learn_button.isEnabled()


def test_a_new_report_keeps_the_other_categorys_learning_and_shows_its_own_status(
    qtbot, runner, result, fakes, counted
):
    screen = _learning_on_screen(qtbot, runner, result, fakes)
    screen.show_result(_missing(result, "чайник"), _RUN)
    assert screen.learning.running("ирригатор")
    assert "Изучаю" not in _status(screen)
    assert screen.learn_button.isEnabled()
    screen.show_result(_missing(result, "ирригатор"), _RUN)  # back to the first one
    assert "Изучаю категорию" in _status(screen)
    fakes.release("ирригатор")
    qtbot.waitUntil(lambda: screen.learn_button.text() == _RERUN, timeout=_WAIT_MS)


def test_finishing_the_category_on_screen_offers_a_rerun(qtbot, runner, result, fakes, counted):
    screen = _learning_on_screen(qtbot, runner, result, fakes)
    fakes.release("ирригатор")
    qtbot.waitUntil(lambda: screen.learn_button.text() == _RERUN, timeout=_WAIT_MS)
    with qtbot.waitSignal(screen.rerun_requested, timeout=1000):
        screen.learn_button.click()


def test_finishing_another_category_offers_nothing_here(qtbot, runner, result, fakes, counted):
    screen = _learning_on_screen(qtbot, runner, result, fakes)
    screen.show_result(_missing(result, "чайник"), _RUN)
    fakes.release("ирригатор")
    qtbot.waitUntil(lambda: not screen.learning.running("ирригатор"), timeout=_WAIT_MS)
    assert screen.learn_button.text() == "Изучить категорию"
    assert screen.learn_button.isEnabled()
    assert "Готово" not in _status(screen)


def test_a_failed_learn_says_so_in_words(qtbot, runner, result, fakes, counted):
    fakes.outcome = False
    screen = _learning_on_screen(qtbot, runner, result, fakes)
    fakes.release("ирригатор")
    qtbot.waitUntil(lambda: "Не удалось" in _status(screen), timeout=_WAIT_MS)
    assert screen.learn_button.isEnabled()
    assert screen.learn_button.text() == "Изучить категорию"
    assert screen.learn_status.property("state") == "error"


def test_the_new_controls_have_accessible_names(qtbot, runner, result):
    screen = _screen(qtbot, runner)
    screen.show_result(_missing(result, "ирригатор"), _RUN)
    assert screen.learn_button.accessibleName()
    assert screen.learn_status.accessibleName()


def _window(qtbot, runner, monkeypatch, answer):
    asked = []
    monkeypatch.setattr(gui_app.prefs, "get_all", _no_prefs)
    monkeypatch.setattr(
        gui_app.QMessageBox, "question", lambda *args, **kwargs: asked.append(args) or answer
    )
    window = gui_app.MainWindow(runner)
    qtbot.addWidget(window)
    window.show()
    return window, asked


def test_closing_while_learning_asks_and_stays_open_on_no(qtbot, runner, monkeypatch):
    window, asked = _window(qtbot, runner, monkeypatch, gui_app.QMessageBox.StandardButton.No)
    monkeypatch.setattr(window.learning, "any_running", lambda: True)
    assert window.close() is False
    assert len(asked) == 1
    assert "изуч" in asked[0][2].lower()


def test_closing_while_learning_closes_on_yes(qtbot, runner, monkeypatch):
    window, asked = _window(qtbot, runner, monkeypatch, gui_app.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(window.learning, "any_running", lambda: True)
    assert window.close() is True
    assert len(asked) == 1


def test_the_default_guard_keeps_real_learning_out(qtbot, runner, result):
    screen = _screen(qtbot, runner)
    screen.show_result(_missing(result, "ирригатор"), _RUN)  # claude_available is False in tests
    qtbot.wait(150)
    assert not screen.learning.any_running()


def test_learn_now_counts_one_start_and_learns(qtbot, runner, fakes, counted):
    learning = CategoryLearning(runner)
    learning.learn_now("кофемолка")
    learning.learn_now("кофемолка")  # already running: neither a second learn nor a count
    qtbot.waitUntil(lambda: fakes.learned == ["кофемолка"], timeout=_WAIT_MS)
    assert counted == [1]
    with qtbot.waitSignal(learning.settled, timeout=_WAIT_MS):
        fakes.release("кофемолка")
    assert learning.outcome("кофемолка") == ""


def test_cancel_stops_the_learn_and_says_so(qtbot, runner, fakes, counted):
    learning = CategoryLearning(runner)
    learning.learn_now("кофемолка")
    qtbot.waitUntil(lambda: fakes.learned == ["кофемолка"], timeout=_WAIT_MS)
    with qtbot.waitSignal(learning.settled, timeout=_WAIT_MS):
        learning.cancel("кофемолка")
    assert not learning.running("кофемолка")
    assert learning.outcome("кофемолка") == gui_learning.LEARN_CANCELLED


def test_cancel_of_a_category_not_learning_does_nothing(runner):
    learning = CategoryLearning(runner)
    learning.cancel("кофемолка")
    assert learning.outcome("кофемолка") is None


def test_a_used_up_hourly_limit_is_said_in_the_banner(qtbot, runner, result, monkeypatch):
    monkeypatch.setattr(report_screen, "over_limit", lambda: True)
    screen = _screen(qtbot, runner)
    screen.show_result(_missing(result, "ирригатор"), _RUN)
    assert "лимит" in _status(screen)
    assert screen.learn_button.isEnabled()  # learning by hand stays


def test_a_zero_hourly_limit_says_auto_learning_is_off(qtbot, runner, result, monkeypatch):
    monkeypatch.setattr(report_screen, "over_limit", lambda: True)
    monkeypatch.setenv("BUYSMARTER_AUTO_LEARN_PER_HOUR", "0")
    screen = _screen(qtbot, runner)
    screen.show_result(_missing(result, "ирригатор"), _RUN)
    assert "выключено" in _status(screen)


def test_a_learning_that_ended_before_its_report_still_offers_the_rerun(
    qtbot, runner, result, fakes, counted
):
    # «Искать без изучения»: the learning may end while the search still runs (#26 live run)
    screen = _screen(qtbot, runner)
    screen.learning.learn_now("ирригатор")
    qtbot.waitUntil(lambda: fakes.learned == ["ирригатор"], timeout=_WAIT_MS)
    fakes.release("ирригатор")
    qtbot.waitUntil(lambda: not screen.learning.running("ирригатор"), timeout=_WAIT_MS)

    screen.show_result(_missing(result, "ирригатор"), _RUN)

    assert screen.learn_button.text() == _RERUN
    assert "Готово" in _status(screen)
