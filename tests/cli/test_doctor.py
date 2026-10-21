"""Tests for the `doctor` command."""

from market_sources.ozon.browser import ozon_state_path
from typer.testing import CliRunner

from buysmarter.cli import main
from buysmarter.services import doctor, ozon_browser
from buysmarter.services.doctor import Check
from buysmarter.services.ozon_browser import CHROME, CHROMIUM


def test_doctor_lists_every_check_with_hint_for_missing(monkeypatch):
    monkeypatch.setattr(
        main,
        "doctor_checks",
        lambda: [
            Check("Storage", True, "unused", "SQLite /x.db"),
            Check("claude CLI", False, "install Claude Code"),
        ],
    )
    result = CliRunner().invoke(main.app, ["doctor"])
    assert result.exit_code == 0
    assert "Storage" in result.output and "ok  SQLite /x.db" in result.output
    assert "missing" in result.output and "install Claude Code" in result.output


def test_chromium_hint_uses_this_interpreter_so_it_works_outside_a_clone(monkeypatch):
    monkeypatch.setattr(doctor, "schema_is_current", lambda url: _done(True))
    hints = {check.name: check.hint for check in doctor.doctor_checks()}
    expected = f'"{doctor.sys.executable}" -m playwright install chromium'
    assert expected in hints[doctor.OZON_BROWSER]


def _installed(monkeypatch, *browsers):
    monkeypatch.setattr(ozon_browser, "launches", lambda browser: browser in browsers)


def test_ozon_browser_row_names_chrome_once_a_session_is_saved(monkeypatch):
    _installed(monkeypatch, CHROME, CHROMIUM)
    ozon_state_path().write_text("{}")
    assert doctor.ozon_browser_check() == Check(doctor.OZON_BROWSER, True, "", "Google Chrome")


def test_ozon_browser_row_names_the_bundled_chromium(monkeypatch):
    _installed(monkeypatch, CHROMIUM)
    check = doctor.ozon_browser_check()
    assert (check.ok, check.detail) == (True, "Playwright Chromium")


def test_chrome_without_a_session_turns_ozon_off_until_setup(monkeypatch):
    _installed(monkeypatch, CHROME)
    check = doctor.ozon_browser_check()
    assert not check.ok
    # install.sh / install.ps1 match this prefix to skip the Chromium prompt
    assert check.hint.startswith("Ozon off until set up")
    assert "`buysmarter ozon-setup`" in check.hint


def test_no_browser_turns_ozon_off_and_suggests_google_chrome():
    check = doctor.ozon_browser_check()
    assert not check.ok
    assert check.hint.startswith("Ozon off; install Google Chrome")


class _RecordingOzonBrowser:
    made: list = []

    def __init__(self, **kwargs):
        self.made.append(kwargs)

    def composer_url(self, path):
        return path

    async def bootstrap(self, url):
        return True


async def test_ozon_setup_opens_chrome_when_installed(monkeypatch):
    _installed(monkeypatch, CHROME, CHROMIUM)
    _RecordingOzonBrowser.made = []
    monkeypatch.setattr(doctor, "OzonBrowser", _RecordingOzonBrowser)
    assert await doctor.pass_ozon_check() is True
    assert [kw["channel"] for kw in _RecordingOzonBrowser.made] == ["chrome"]


async def test_ozon_setup_without_a_browser_fails_without_launching(monkeypatch):
    _RecordingOzonBrowser.made = []
    monkeypatch.setattr(doctor, "OzonBrowser", _RecordingOzonBrowser)
    assert await doctor.pass_ozon_check() is False
    assert _RecordingOzonBrowser.made == []


def test_ozon_setup_command_without_a_browser_says_what_to_install():
    result = CliRunner().invoke(main.app, ["ozon-setup"])
    assert result.exit_code == 1
    assert "Google Chrome" in result.output


async def _done(value):
    return value


def test_failing_checks_carry_a_russian_hint_that_points_inside_the_app(monkeypatch):
    monkeypatch.setattr(doctor, "schema_is_current", lambda url: _done(False))
    _installed(monkeypatch, CHROME)
    hints = {c.name: c.app_hint for c in doctor.doctor_checks() if not c.ok}
    assert "«Пройти проверку Ozon»" in hints[doctor.OZON_BROWSER]
    assert "«Пройти проверку Ozon»" in hints["Ozon session"]
    assert "«AI-ключ»" in hints["LLM"]
    assert "История" in hints[doctor.STORAGE]
    assert not any("`" in hint for hint in hints.values())  # no commands to type


def test_no_browser_app_hint_asks_for_google_chrome():
    assert "Google Chrome" in doctor.ozon_browser_check().app_hint


def test_a_server_database_url_app_hint_names_the_settings_file(monkeypatch):
    monkeypatch.setattr(doctor, "schema_is_current", lambda url: _done(False))
    check = doctor._storage_check("postgresql+asyncpg://u:p@127.0.0.1:5434/x")
    assert ".env" in check.app_hint and "DATABASE_URL" in check.app_hint
