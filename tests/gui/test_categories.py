"""«Категории»: the learned categories, their summary, learning and switches (parity #11)."""

from pathlib import Path

from buysmarter.capabilities.provenance import CHANGED_NOTE
from buysmarter.gui.bridge import USER_ERROR
from buysmarter.gui.learning import CategoryLearning
from buysmarter.gui.screens import categories as screen_module
from buysmarter.gui.screens.categories import (
    EMPTY,
    LEARN_TEXT,
    RELEARN_TEXT,
    CategoriesScreen,
)
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.knowledge.store import save_knowledge

_WAIT_MS = 3000


def _save(runner, qtbot, slug: str) -> None:
    brand = BrandBook(tiers={"Roborock": "A", "Xiaomi": "C"}, aliases={})
    job = runner.submit(save_knowledge(slug, brand, ModelBook(), "opus"))
    with qtbot.waitSignal(job.finished, timeout=_WAIT_MS):
        pass


def _screen(qtbot, runner, picked: str = "") -> CategoriesScreen:
    screen = CategoriesScreen(runner, CategoryLearning(runner), pick_file=lambda _parent: picked)
    qtbot.addWidget(screen)
    screen.reload()
    return screen


def _rows(screen: CategoriesScreen) -> list[str]:
    return [screen.table.item(r, 0).text() for r in range(screen.table.rowCount())]


def _open(qtbot, screen: CategoriesScreen, slug: str) -> None:
    qtbot.waitUntil(lambda: slug in _rows(screen), timeout=_WAIT_MS)
    screen.table.selectRow(_rows(screen).index(slug))
    qtbot.waitUntil(lambda: screen.toggles is not None, timeout=_WAIT_MS)


def test_nothing_learned_says_how_to_start(qtbot, runner) -> None:
    screen = _screen(qtbot, runner)

    qtbot.waitUntil(lambda: screen.empty.text() == EMPTY, timeout=_WAIT_MS)
    assert screen.table.isHidden()


def test_the_list_names_each_category_with_its_books(qtbot, runner) -> None:
    _save(runner, qtbot, "робот-пылесос")
    screen = _screen(qtbot, runner)

    qtbot.waitUntil(lambda: _rows(screen) == ["робот-пылесос"], timeout=_WAIT_MS)
    assert screen.table.item(0, 1).text() == "бренды 2, модели 0"


def test_a_picked_category_shows_its_summary_and_switches_without_a_rerun(qtbot, runner) -> None:
    _save(runner, qtbot, "робот-пылесос")
    screen = _screen(qtbot, runner)

    _open(qtbot, screen, "робот-пылесос")

    assert screen.name.text() == "робот-пылесос"
    assert screen.learn_button.text() == RELEARN_TEXT
    assert "Roborock" in screen.tiers.item(0, 2).text()
    assert "моделей: 0" in screen.counts.text()
    screen.toggles.checkboxes["used_market"].click()
    qtbot.waitUntil(lambda: screen.toggles.note.text() == CHANGED_NOTE, timeout=_WAIT_MS)
    assert screen.toggles.rerun_button.isHidden()


def test_learn_uses_the_web_box_and_refreshes_the_list(qtbot, runner, counted, monkeypatch):
    calls = []

    async def _learn(category, *, web_search=True):
        calls.append((category, web_search))
        await save_knowledge(category, BrandBook(tiers={"Bosch": "A"}), ModelBook(), "opus")

    monkeypatch.setattr(screen_module, "learn_category", _learn)
    screen = _screen(qtbot, runner)
    screen.name.setText("кофемашина")
    assert screen.learn_button.text() == LEARN_TEXT
    screen.web.setChecked(False)

    screen.learn_button.click()

    qtbot.waitUntil(lambda: "кофемашина" in _rows(screen), timeout=_WAIT_MS)
    assert calls == [("кофемашина", False)]
    assert counted == []  # by hand: not in the hourly limit
    qtbot.waitUntil(lambda: screen.toggles is not None, timeout=_WAIT_MS)
    assert "Bosch" in screen.tiers.item(0, 2).text()


def test_a_failed_learn_says_so_without_internals(qtbot, runner, monkeypatch) -> None:
    async def _broken(category, *, web_search=True):
        raise RuntimeError("secret stack detail")

    monkeypatch.setattr(screen_module, "learn_category", _broken)
    screen = _screen(qtbot, runner)
    screen.name.setText("кофемашина")

    screen.learn_button.click()

    qtbot.waitUntil(lambda: screen.status.text() == USER_ERROR, timeout=_WAIT_MS)
    assert screen.learn_button.isEnabled()


def test_notes_learn_the_picked_file_into_the_named_category(qtbot, runner, monkeypatch) -> None:
    calls = []

    async def _notes(path, category):
        calls.append((path, category))

    monkeypatch.setattr(screen_module, "learn_from_notes", _notes)
    screen = _screen(qtbot, runner, picked="/tmp/notes.md")
    screen.name.setText("кофемашина")

    screen.notes_button.click()

    qtbot.waitUntil(lambda: calls == [(Path("/tmp/notes.md"), "кофемашина")], timeout=_WAIT_MS)


def test_no_file_picked_or_no_name_learns_nothing(qtbot, runner, monkeypatch) -> None:
    calls = []

    async def _notes(path, category):
        calls.append(category)

    monkeypatch.setattr(screen_module, "learn_from_notes", _notes)
    screen = _screen(qtbot, runner)  # the picker is cancelled
    screen.name.setText("кофемашина")
    screen.notes_button.click()
    screen.name.clear()
    screen.learn_button.click()

    qtbot.wait(100)
    assert calls == []
    assert screen.status.text() == screen_module.NO_NAME


def test_a_running_learn_can_be_cancelled(qtbot, runner, monkeypatch) -> None:
    from buysmarter.gui.learning import LEARN_CANCELLED

    async def _forever(category, *, web_search=True):
        import asyncio

        await asyncio.Event().wait()

    monkeypatch.setattr(screen_module, "learn_category", _forever)
    screen = _screen(qtbot, runner)
    screen.name.setText("кофемашина")
    assert screen.cancel_button.isHidden()

    screen.learn_button.click()
    qtbot.waitUntil(lambda: not screen.cancel_button.isHidden(), timeout=_WAIT_MS)
    screen.cancel_button.click()

    qtbot.waitUntil(lambda: screen.status.text() == LEARN_CANCELLED, timeout=_WAIT_MS)
    assert screen.cancel_button.isHidden()
    assert screen.learn_button.isEnabled()


def test_a_store_that_cannot_be_read_is_said(qtbot, runner, monkeypatch) -> None:
    async def _down():
        raise OSError("disk detail")

    monkeypatch.setattr(screen_module, "stored_rows", _down)
    screen = _screen(qtbot, runner)

    qtbot.waitUntil(lambda: screen.status.text() == USER_ERROR, timeout=_WAIT_MS)
