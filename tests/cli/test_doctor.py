"""Tests for the `doctor` command."""

from typer.testing import CliRunner

from buysmarter.cli import main
from buysmarter.services import doctor
from buysmarter.services.doctor import Check


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
    monkeypatch.setattr(doctor, "_chromium_installed", lambda: False)
    hints = {check.name: check.hint for check in doctor.doctor_checks()}
    expected = f'"{doctor.sys.executable}" -m playwright install chromium'
    assert expected in hints["Playwright Chromium"]


async def _done(value):
    return value
