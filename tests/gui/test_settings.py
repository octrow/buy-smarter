"""Settings screen: doctor rows with hints, Ozon availability, the visible Ozon check."""

import threading

from buysmarter.gui import app as gui_app
from buysmarter.gui.screens.settings import SettingsScreen
from buysmarter.services import doctor
from buysmarter.services.doctor import OZON_BROWSER, Check
from tests.gui.conftest import WAIT_MS

_NO_BROWSER = [
    Check("Storage", True, "unused", "SQLite /data/buysmarter.db"),
    Check("LLM", False, "LLM parsing off; run `buysmarter llm set-key <provider>`"),
    Check(OZON_BROWSER, False, "Ozon off; run `playwright install chromium`"),
]


def test_rows_show_detail_when_ok_and_the_hint_when_missing(qtbot, runner):
    screen = SettingsScreen(runner, checks=lambda: _NO_BROWSER)
    qtbot.addWidget(screen)
    with qtbot.waitSignal(screen.checks_ready, timeout=WAIT_MS):
        screen.recheck()
    assert screen.row_texts() == {
        "Storage": ("ок", "SQLite /data/buysmarter.db"),
        "LLM": ("нет", "LLM parsing off; run `buysmarter llm set-key <provider>`"),
        OZON_BROWSER: ("нет", "Ozon off; run `playwright install chromium`"),
    }


def test_missing_ozon_browser_turns_ozon_off_in_search(qtbot, runner, monkeypatch):
    monkeypatch.setattr(doctor, "doctor_checks", lambda: _NO_BROWSER)
    window = gui_app.MainWindow(runner)
    qtbot.addWidget(window)
    ozon = window.search.sources["ozon"]
    qtbot.waitUntil(lambda: not ozon.isEnabled(), timeout=WAIT_MS)
    assert window.search.status["ozon"].text() == "недоступен"
    assert not ozon.isChecked()
    assert "playwright install chromium" in ozon.toolTip()


def test_ozon_check_reports_the_saved_session_and_rechecks(qtbot, runner):
    runs = []

    def checks():
        runs.append(True)
        return _NO_BROWSER

    async def passed():
        return True

    screen = SettingsScreen(runner, checks=checks, ozon_check=passed)
    qtbot.addWidget(screen)
    screen.pass_ozon()
    qtbot.waitUntil(lambda: runs == [True], timeout=WAIT_MS)
    assert screen.ozon_message.text() == "Сессия Ozon сохранена."
    assert screen.ozon_button.isEnabled()


def test_ozon_check_timeout_says_so(qtbot, runner):
    async def timed_out():
        return False

    screen = SettingsScreen(runner, checks=lambda: [], ozon_check=timed_out)
    qtbot.addWidget(screen)
    screen.pass_ozon()
    qtbot.waitUntil(screen.ozon_button.isEnabled, timeout=WAIT_MS)
    assert screen.ozon_message.text().startswith("Не удалось получить сессию Ozon")


def test_checks_run_one_at_a_time(qtbot, runner):
    inside, overlap = threading.Lock(), []

    def checks():
        if not inside.acquire(blocking=False):
            overlap.append(True)
            return []
        try:
            threading.Event().wait(0.05)
            return []
        finally:
            inside.release()

    screens = [SettingsScreen(runner, checks=checks) for _ in range(3)]
    for screen in screens:
        qtbot.addWidget(screen)
        screen.recheck()
    qtbot.waitUntil(lambda: all(s.recheck_button.isEnabled() for s in screens), timeout=WAIT_MS)
    assert overlap == []
