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
