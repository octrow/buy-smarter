"""The desktop app starts and exits headless."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from buysmarter.gui import app as gui_app


def test_run_shows_the_main_window_and_returns_the_exit_code(qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(gui_app.MainWindow, "show", lambda self: shown.append(self))
    QTimer.singleShot(0, QApplication.quit)
    assert gui_app.run() == 0
    assert [w.windowTitle() for w in shown] == ["buy-smarter"]


def test_main_prepares_logging_and_the_store_before_the_window(monkeypatch):
    calls = []
    monkeypatch.setattr(gui_app, "setup_logging", lambda: calls.append("logging"))
    monkeypatch.setattr(gui_app, "new_run_id", lambda: calls.append("run id"))
    monkeypatch.setattr(gui_app.local_store, "prepare", lambda: calls.append("store"))
    monkeypatch.setattr(gui_app, "run", lambda: calls.append("window") or 0)
    assert gui_app.main() == 0
    assert calls == ["logging", "run id", "store", "window"]
