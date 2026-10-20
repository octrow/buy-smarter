"""The `gui` command."""

import builtins

import pytest
from typer.testing import CliRunner

from buysmarter.cli import main


def test_gui_command_runs_the_app_and_passes_its_exit_code(monkeypatch):
    pytest.importorskip("PySide6")
    from buysmarter.gui import app as gui_app

    monkeypatch.setattr(gui_app, "run", lambda: 3)
    result = CliRunner().invoke(main.app, ["gui"])
    assert result.exit_code == 3


def test_gui_command_without_the_extra_says_how_to_install_it(monkeypatch):
    real_import = builtins.__import__

    def no_pyside(name, *args, **kwargs):
        if name.startswith(("PySide6", "buysmarter.gui")):
            raise ModuleNotFoundError(f"No module named {name!r}", name="PySide6")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pyside)
    result = CliRunner().invoke(main.app, ["gui"])
    assert result.exit_code == 1
    assert "buysmarter[gui]" in result.output
