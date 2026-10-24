"""Preferences screen: edits are stored and the next search (and next start) uses them."""

from buysmarter.gui import app as gui_app
from buysmarter.gui.screens.preferences import PreferencesScreen
from tests.gui.conftest import WAIT_MS


def test_saved_prefs_reach_search_after_recreating_the_window(qtbot, runner):
    first = gui_app.MainWindow(runner)
    qtbot.addWidget(first)
    page = first.preferences
    page.city.setText("Шымкент")
    page.budget.setValue(50000)
    page.sources["olx"].setChecked(False)
    page.excluded.setText("Noname,  Generic ,")
    with qtbot.waitSignal(page.saved, timeout=WAIT_MS):
        page.save()
    assert "Шымкент" in first.search.city.placeholderText()  # the open window's next search too

    second = gui_app.MainWindow(runner)
    qtbot.addWidget(second)
    qtbot.waitUntil(lambda: "Шымкент" in second.search.city.placeholderText(), timeout=WAIT_MS)
    assert "50\xa0000" in second.search.budget.specialValueText()
    assert not second.search.sources["olx"].isChecked()
    assert second.preferences.excluded.text() == "Noname, Generic"


def test_a_store_that_cannot_be_written_says_so(qtbot, runner):
    async def broken(values):
        raise OSError("disk full at /secret/path")

    page = PreferencesScreen(runner, store=broken)
    qtbot.addWidget(page)
    page.save()
    qtbot.waitUntil(page.save_button.isEnabled, timeout=WAIT_MS)
    assert page.message.text().startswith("Не удалось сохранить")
    assert "/secret/path" not in page.message.text()


def test_saving_needs_at_least_one_marketplace(qtbot, runner):
    page = PreferencesScreen(runner)
    qtbot.addWidget(page)
    for box in page.sources.values():
        box.setChecked(False)
    page.save()
    assert page.message.text() == "Выберите хотя бы один маркетплейс."
