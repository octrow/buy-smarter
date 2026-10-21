"""The desktop app starts and exits headless."""

import os
import subprocess

from PySide6.QtCore import Qt, QTimer
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


def test_a_finished_search_saves_the_cli_report_and_lists_it_in_past_searches(
    qtbot, runner, result
):
    from buysmarter.cli.report import saved_reports

    window = gui_app.MainWindow(runner)
    qtbot.addWidget(window)
    window.search.report_ready.emit(result, SearchRun("ирригатор до 40000", True, {}))

    [path] = saved_reports()
    assert path.name.endswith("-ирригатор-до-40000.md")
    assert "Отчёт сохранён" in window.search.message.text()
    window.saved.reload()
    assert window.saved.reports.count() == 1
    assert "Philips Sonicare Power Flosser" in window.saved.view.toPlainText()


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
    shown = []  # keeps the window alive: freed mid-teardown with jobs pending, it crashes Qt
    monkeypatch.setattr(gui_app.MainWindow, "show", lambda self: shown.append(self))
    QTimer.singleShot(0, QApplication.quit)
    gui_app.run()
    # Wayland docks match the window to buysmarter.desktop (the .deb's menu entry) by this name.
    assert QApplication.desktopFileName() == "buysmarter"
    assert not QApplication.windowIcon().isNull()


def test_a_failing_store_check_warns_on_the_screens_that_need_the_store(qtbot, runner):
    window = gui_app.MainWindow(runner)
    qtbot.addWidget(window)
    with qtbot.waitSignal(window.settings.checks_ready, timeout=WAIT_MS):
        pass  # the start-up checks (healthy) land before the ones below
    pages = (window.preferences, window.history)
    window._apply_checks([gui_app.Check("Storage", False, "start Postgres")])
    assert all(not page.store_warning.isHidden() for page in pages)
    window._apply_checks([gui_app.Check("Storage", True, "", "SQLite x.db")])
    assert all(page.store_warning.isHidden() for page in pages)


def _window_with_search(qtbot, runner, monkeypatch, *, running: bool, answer):
    asked = []
    monkeypatch.setattr(gui_app.prefs, "get_all", _no_prefs)
    monkeypatch.setattr(
        gui_app.QMessageBox, "question", lambda *args, **kwargs: asked.append(args) or answer
    )
    window = gui_app.MainWindow(runner)
    qtbot.addWidget(window)
    monkeypatch.setattr(window.search, "is_running", lambda: running)
    window.show()
    return window, asked


def test_closing_during_a_search_asks_and_stays_open_on_no(qtbot, runner, monkeypatch):
    window, asked = _window_with_search(
        qtbot, runner, monkeypatch, running=True, answer=gui_app.QMessageBox.StandardButton.No
    )
    assert window.close() is False
    assert len(asked) == 1
    assert window.isVisible()


def test_closing_during_a_search_closes_on_yes(qtbot, runner, monkeypatch):
    window, asked = _window_with_search(
        qtbot, runner, monkeypatch, running=True, answer=gui_app.QMessageBox.StandardButton.Yes
    )
    assert window.close() is True
    assert len(asked) == 1


def test_closing_while_idle_does_not_ask(qtbot, runner, monkeypatch):
    window, asked = _window_with_search(qtbot, runner, monkeypatch, running=False, answer=None)
    assert window.close() is True
    assert asked == []


def _nav_items(window):
    return [window.sidebar.item(row) for row in range(window.sidebar.count())]


def test_sidebar_groups_screens_under_headers_that_cannot_be_selected(qtbot, runner, monkeypatch):
    monkeypatch.setattr(gui_app.prefs, "get_all", _no_prefs)
    window = gui_app.MainWindow(runner)
    qtbot.addWidget(window)
    headers = [i.text() for i in _nav_items(window) if not i.flags() & Qt.ItemFlag.ItemIsSelectable]
    assert headers == ["ПОКУПКИ", "СЕРВИС"]
    assert window.sidebar.currentItem().text() == "Поиск"


def test_each_nav_item_has_an_icon_and_opens_its_screen(qtbot, runner, monkeypatch):
    monkeypatch.setattr(gui_app.prefs, "get_all", _no_prefs)
    window = gui_app.MainWindow(runner)
    qtbot.addWidget(window)
    screens = [i for i in _nav_items(window) if i.flags() & Qt.ItemFlag.ItemIsSelectable]
    assert len(screens) == window.stack.count()
    for item in screens:
        assert not item.icon().isNull()
        window.sidebar.setCurrentItem(item)
        assert window.stack.currentIndex() == screens.index(item)


def test_opening_a_screen_selects_its_nav_item(qtbot, runner, monkeypatch, result):
    monkeypatch.setattr(gui_app.prefs, "get_all", _no_prefs)
    window = gui_app.MainWindow(runner)
    qtbot.addWidget(window)
    window._show_screen(window.report)
    assert window.sidebar.currentItem().text() == "Отчёт"
    assert window.stack.currentWidget() is window.report
