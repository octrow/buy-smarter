"""Auto-learning from the report screen: per category, a new report keeps the other's learning."""

import asyncio

import pytest

from buysmarter.gui import app as gui_app
from buysmarter.gui import learning as gui_learning
from buysmarter.gui.learning import CategoryLearning
from buysmarter.gui.screens.report import ReportScreen
from buysmarter.gui.screens.search import SearchRun
from tests.gui.test_app import _no_prefs

_RUN = SearchRun("ирригатор", True, {"kaspi": "done"})
_WAIT_MS = 3000
_RERUN = "Повторить поиск"


class Fakes:
    """Stand-ins for the decision and the learn; a learn waits on a gate per category."""

    def __init__(self) -> None:
        self.allowed = True
        self.outcome = True
        self.decided: list[str] = []
        self.learned: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}

    async def decide(self, category, **_kw):
        self.decided.append(category)
        return self.allowed

    async def learn(self, category):
        self.learned.append(category)
        gate = self.gates.setdefault(category, asyncio.Event())
        await gate.wait()
        return self.outcome

    def release(self, category):
        gate = self.gates[category]
        gate._loop.call_soon_threadsafe(gate.set)  # the gate lives on the core loop


@pytest.fixture
def fakes(monkeypatch):
    f = Fakes()
    monkeypatch.setattr(gui_learning, "should_auto_learn", f.decide)
    monkeypatch.setattr(gui_learning, "auto_learn", f.learn)
    return f


@pytest.fixture
def counted(monkeypatch):
    starts = []
    monkeypatch.setattr(gui_learning, "count_start", lambda: starts.append(1))
    return starts


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
    screen = _screen(qtbot, runner)
    screen.show_result(_missing(result, category), _RUN)
    qtbot.waitUntil(lambda: fakes.learned == [category], timeout=_WAIT_MS)
    return screen


def test_a_report_with_missing_knowledge_starts_learning_and_counts_it(
    qtbot, runner, result, fakes, counted
):
    screen = _learning_on_screen(qtbot, runner, result, fakes)
    qtbot.waitUntil(lambda: "Изучаю категорию" in _status(screen), timeout=_WAIT_MS)
    assert counted == [1]
    assert not screen.learn_button.isEnabled()
    fakes.release("ирригатор")


def test_no_auto_start_when_the_condition_does_not_hold(qtbot, runner, result, fakes, counted):
    fakes.allowed = False
    screen = _screen(qtbot, runner)
    screen.show_result(_missing(result, "ирригатор"), _RUN)
    qtbot.waitUntil(lambda: fakes.decided == ["ирригатор"], timeout=_WAIT_MS)
    qtbot.wait(100)
    assert fakes.learned == []
    assert counted == []
    assert screen.learn_button.isEnabled()  # the manual button stays


def test_no_auto_start_when_an_llm_step_was_skipped(qtbot, runner, result, fakes, counted):
    skipped = _missing(result, "ирригатор").model_copy(update={"skipped_llm_steps": ["review"]})
    screen = _screen(qtbot, runner)
    screen.show_result(skipped, _RUN)
    qtbot.wait(100)
    assert fakes.decided == []
    assert not screen.learn_button.isEnabled()


def test_a_new_report_keeps_the_other_categorys_learning_and_shows_its_own_status(
    qtbot, runner, result, fakes, counted
):
    screen = _learning_on_screen(qtbot, runner, result, fakes)
    fakes.allowed = False
    screen.show_result(_missing(result, "чайник"), _RUN)
    qtbot.waitUntil(lambda: fakes.decided == ["ирригатор", "чайник"], timeout=_WAIT_MS)
    qtbot.wait(100)
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
    fakes.allowed = False
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


def test_the_new_controls_have_accessible_names(qtbot, runner, result, fakes):
    fakes.allowed = False
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
