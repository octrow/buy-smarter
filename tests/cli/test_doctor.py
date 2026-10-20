"""Tests for the `doctor` command."""

from typer.testing import CliRunner

from buysmarter.cli import main


def test_doctor_lists_every_check_with_hint_for_missing(monkeypatch):
    monkeypatch.setattr(
        main,
        "doctor_checks",
        lambda: [
            main.Check("Storage", True, "unused", "SQLite /x.db"),
            main.Check("claude CLI", False, "install Claude Code"),
        ],
    )
    result = CliRunner().invoke(main.app, ["doctor"])
    assert result.exit_code == 0
    assert "Storage" in result.output and "ok  SQLite /x.db" in result.output
    assert "missing" in result.output and "install Claude Code" in result.output


def test_chromium_hint_uses_this_interpreter_so_it_works_outside_a_clone(monkeypatch):
    monkeypatch.setattr(main, "schema_is_current", lambda url: _done(True))
    monkeypatch.setattr(main, "_chromium_installed", lambda: False)
    hints = {check.name: check.hint for check in main.doctor_checks()}
    assert f'"{main.sys.executable}" -m playwright install chromium' in hints["Playwright Chromium"]


async def _done(value):
    return value
