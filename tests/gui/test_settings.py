"""Settings screen: doctor rows with hints, Ozon availability, the visible Ozon check."""

import threading

import keyring
from keyring.backends.fail import Keyring as NoBackend
from PySide6.QtWidgets import QLabel, QLineEdit

from buysmarter.gui import app as gui_app
from buysmarter.gui.screens.settings import SettingsScreen
from buysmarter.llm import keys
from buysmarter.services import doctor
from buysmarter.services.doctor import (
    OZON_BROWSER,
    OZON_NO_BROWSER,
    OZON_SAVED,
    OZON_TIMEOUT,
    Check,
    ozon_setup_message,
)
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


def test_a_failing_row_shows_its_in_app_hint_when_it_has_one(qtbot, runner):
    checks = [Check("LLM", False, "run `buysmarter llm set-key`", app_hint="Добавьте ключ ниже")]
    screen = SettingsScreen(runner, checks=lambda: checks)
    qtbot.addWidget(screen)
    with qtbot.waitSignal(screen.checks_ready, timeout=WAIT_MS):
        screen.recheck()
    assert screen.row_texts()["LLM"] == ("нет", "Добавьте ключ ниже")


def test_the_style_row_reads_in_russian(qtbot, runner, monkeypatch):
    monkeypatch.setattr(doctor.style, "available", lambda: True)
    monkeypatch.setattr(doctor.weights, "missing", lambda: True)
    checks = [doctor.style_check()]
    screen = SettingsScreen(runner, checks=lambda: checks)
    qtbot.addWidget(screen)
    with qtbot.waitSignal(screen.checks_ready, timeout=WAIT_MS):
        screen.recheck()
    name = screen._row_labels[doctor.STYLE][0].text()
    assert (name, screen.row_texts()[doctor.STYLE]) == (
        "Стиль одежды",
        ("ок", "модель скачается при первом добавлении фото (~200\u00a0МБ)"),
    )


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
        return OZON_SAVED

    screen = SettingsScreen(runner, checks=checks, ozon_check=passed)
    qtbot.addWidget(screen)
    screen.pass_ozon()
    qtbot.waitUntil(lambda: runs == [True], timeout=WAIT_MS)
    assert screen.ozon_message.text() == ozon_setup_message(OZON_SAVED)  # with the session path
    assert screen.ozon_button.isEnabled()


def test_ozon_check_timeout_says_so(qtbot, runner):
    async def timed_out():
        return OZON_TIMEOUT

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


_KEY = "sk-proj-GUISECRET123456"


def _llm_screen(qtbot, runner) -> SettingsScreen:
    screen = SettingsScreen(runner, checks=lambda: [doctor.llm_check()])
    qtbot.addWidget(screen)
    return screen


def _all_texts(screen: SettingsScreen) -> str:
    texts = [w.text() for w in screen.findChildren(QLabel) + screen.findChildren(QLineEdit)]
    return " ".join(texts)


def test_the_key_field_is_masked(qtbot, runner):
    screen = _llm_screen(qtbot, runner)
    assert screen.key_input.echoMode() == QLineEdit.EchoMode.Password


def test_saving_a_key_stores_it_hides_it_and_turns_the_llm_row_green(qtbot, runner, fake_keyring):
    screen = _llm_screen(qtbot, runner)
    screen.provider.setCurrentText("gemini")
    screen.key_input.setText(_KEY)
    with qtbot.waitSignal(screen.checks_ready, timeout=WAIT_MS):
        screen.save_key()
    assert fake_keyring.secrets[("buysmarter", "gemini")] == _KEY
    assert screen.key_input.text() == ""
    assert "GUISECRET" not in _all_texts(screen)
    assert screen.row_texts()["LLM"] == ("ок", "gemini (keyring)")


def test_clearing_the_key_turns_the_llm_row_back_to_missing(qtbot, runner):
    keys.set_key("openai", _KEY)
    screen = _llm_screen(qtbot, runner)
    screen.provider.setCurrentText("openai")
    with qtbot.waitSignal(screen.checks_ready, timeout=WAIT_MS):
        screen.clear_key()
    assert keys.get_key("openai") is None
    assert screen.row_texts()["LLM"][0] == "нет"


def test_an_empty_key_is_not_saved(qtbot, runner, fake_keyring):
    screen = _llm_screen(qtbot, runner)
    screen.save_key()
    assert fake_keyring.secrets == {}
    assert screen.key_message.text() == "Введите ключ."


def test_without_a_keyring_the_message_names_the_env_var(qtbot, runner):
    keyring.set_keyring(NoBackend())
    screen = _llm_screen(qtbot, runner)
    screen.provider.setCurrentText("openai")
    screen.key_input.setText(_KEY)
    screen.save_key()
    qtbot.waitUntil(screen.save_button.isEnabled, timeout=WAIT_MS)
    assert "OPENAI_API_KEY" in screen.key_message.text()
    assert "GUISECRET" not in _all_texts(screen)


def test_a_recheck_asked_during_a_check_runs_after_it(qtbot, runner):
    release, runs = threading.Event(), []

    def checks():
        runs.append(True)
        if len(runs) == 1:
            release.wait(WAIT_MS / 1000)
        return []

    screen = SettingsScreen(runner, checks=checks)
    qtbot.addWidget(screen)
    screen.recheck()
    screen.recheck()  # e.g. a key saved while the start-up checks run
    release.set()
    qtbot.waitUntil(lambda: len(runs) == 2, timeout=WAIT_MS)


def test_ozon_check_without_a_browser_says_so_as_the_cli(qtbot, runner):
    async def no_browser():
        return OZON_NO_BROWSER

    screen = SettingsScreen(runner, checks=lambda: [], ozon_check=no_browser)
    qtbot.addWidget(screen)
    screen.pass_ozon()
    qtbot.waitUntil(screen.ozon_button.isEnabled, timeout=WAIT_MS)
    assert screen.ozon_message.text() == ozon_setup_message(OZON_NO_BROWSER)
