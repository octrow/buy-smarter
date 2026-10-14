"""Tests for the `doctor` command."""

from typer.testing import CliRunner

from buysmarter.cli import main


def test_doctor_lists_every_check_with_hint_for_missing(monkeypatch):
    monkeypatch.setattr(
        main,
        "doctor_checks",
        lambda: [("Postgres", True, "unused"), ("claude CLI", False, "install Claude Code")],
    )
    result = CliRunner().invoke(main.app, ["doctor"])
    assert result.exit_code == 0
    assert "Postgres" in result.output and "ok" in result.output
    assert "missing" in result.output and "install Claude Code" in result.output


def test_chromium_hint_uses_this_interpreter_so_it_works_outside_a_clone(monkeypatch):
    monkeypatch.setattr(main, "_db_reachable", lambda url: _done(True))
    monkeypatch.setattr(main, "_chromium_installed", lambda: False)
    hints = {name: hint for name, _, hint in main.doctor_checks()}
    assert f'"{main.sys.executable}" -m playwright install chromium' in hints["Playwright Chromium"]


async def _done(value):
    return value
