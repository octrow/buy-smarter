"""The desktop app starts and exits headless."""

import os
import subprocess

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from buysmarter.gui import app as gui_app
from buysmarter.gui.bridge import USER_ERROR
from buysmarter.gui.screens.search import SearchRun
from tests.gui.conftest import WAIT_MS


async def _no_prefs():
    return {}


def test_run_shows_the_main_window_and_returns_the_exit_code(qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(gui_app.prefs, "get_all", _no_prefs)
    monkeypatch.setattr(gui_app.MainWindow, "show", lambda self: shown.append(self))
    QTimer.singleShot(0, QApplication.quit)
    assert gui_app.run() == 0
    assert [w.windowTitle() for w in shown] == ["buy-smarter"]


def test_main_prepares_logging_and_the_store_before_the_window(monkeypatch):
    calls = []
    monkeypatch.setattr(gui_app, "setup_logging", lambda: calls.append("logging"))
    monkeypatch.setattr(gui_app, "new_run_id", lambda: calls.append("run id"))
    monkeypatch.setattr(gui_app, "inherit_login_path", lambda: calls.append("path"))
    monkeypatch.setattr(gui_app.local_store, "prepare", lambda: calls.append("store"))
    monkeypatch.setattr(gui_app, "run", lambda: calls.append("window") or 0)
    assert gui_app.main() == 0
    assert calls == ["logging", "run id", "path", "store", "window"]


def test_a_menu_launch_takes_path_from_the_login_shell_past_rc_banners(monkeypatch):
    def shell(cmd, **_kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="Welcome!\n/nvm/bin:/usr/bin\n")

    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(gui_app.sys, "stdin", None)
    monkeypatch.setattr(gui_app.subprocess, "run", shell)
    gui_app.inherit_login_path()
    assert os.environ["PATH"] == "/nvm/bin:/usr/bin"


def test_window_prefills_search_from_saved_prefs_and_opens_the_report(
    qtbot, runner, result, monkeypatch
):
    async def saved():
        return {"city": "Астана"}

    monkeypatch.setattr(gui_app.prefs, "get_all", saved)
    window = gui_app.MainWindow(runner)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window.search.city.text() == "Астана", timeout=WAIT_MS)

    window.search.report_ready.emit(result, SearchRun("q", True, {"kaspi": "done"}))
    assert window.stack.currentWidget() is window.report
    assert window.report.best_axes.rowCount() == 2


def test_an_unexpected_error_shows_a_dialog_and_the_window_stays_open(qtbot, runner, monkeypatch):
    shown = []
    monkeypatch.setattr(
        gui_app.QMessageBox, "critical", lambda parent, title, text: shown.append(text)
    )
    window = gui_app.MainWindow(runner)
    qtbot.addWidget(window)
    window.show()
    previous = gui_app.install_excepthook(window)
    try:
        try:
            raise RuntimeError("token=abc123")
        except RuntimeError as exc:
            gui_app.sys.excepthook(type(exc), exc, exc.__traceback__)
    finally:
        gui_app.sys.excepthook = previous
    assert shown == [USER_ERROR]
    assert window.isVisible()


def test_ctrl_c_still_reaches_the_previous_hook(qapp, monkeypatch):
    seen = []
    monkeypatch.setattr(gui_app.sys, "excepthook", lambda *args: seen.append(args[0]))
    previous = gui_app.install_excepthook(None)
    try:
        gui_app.sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    finally:
        gui_app.sys.excepthook = previous
    assert seen == [KeyboardInterrupt]


def test_an_error_while_the_dialog_is_open_is_logged_without_a_second_dialog(qapp, monkeypatch):
    shown = []

    def critical(parent, title, text):
        shown.append(text)
        gui_app.sys.excepthook(RuntimeError, RuntimeError("again"), None)  # re-entered

    monkeypatch.setattr(gui_app.QMessageBox, "critical", critical)
    previous = gui_app.install_excepthook(None)
    try:
        gui_app.sys.excepthook(RuntimeError, RuntimeError("first"), None)
    finally:
        gui_app.sys.excepthook = previous
    assert shown == [USER_ERROR]


def test_run_names_the_desktop_entry_and_sets_the_app_icon(qapp, monkeypatch):
    monkeypatch.setattr(gui_app.prefs, "get_all", _no_prefs)
    monkeypatch.setattr(gui_app.MainWindow, "show", lambda self: None)
    QTimer.singleShot(0, QApplication.quit)
    gui_app.run()
    # Wayland docks match the window to buysmarter.desktop (the .deb's menu entry) by this name.
    assert QApplication.desktopFileName() == "buysmarter"
    assert not QApplication.windowIcon().isNull()
