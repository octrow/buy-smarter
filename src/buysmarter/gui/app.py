"""Desktop app entry point: the QApplication and the main window."""

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from buysmarter import local_store
from buysmarter.gui.bridge import USER_ERROR, CoreRunner
from buysmarter.gui.screens.history import HistoryScreen
from buysmarter.gui.screens.preferences import PreferencesScreen
from buysmarter.gui.screens.report import ReportScreen
from buysmarter.gui.screens.search import SearchRun, SearchScreen
from buysmarter.gui.screens.settings import SettingsScreen, store_available, unavailable_sources
from buysmarter.gui.theme import ICON, apply_theme
from buysmarter.logsetup import new_run_id, setup_logging
from buysmarter.memory import prefs
from buysmarter.services.doctor import Check
from buysmarter.services.recommend import RecommendResult

logger = logging.getLogger(__name__)

_TITLE = "buy-smarter"
DESKTOP_ENTRY = "buysmarter"  # packaging/linux/buysmarter.desktop
_MIN_SIZE = (960, 640)
_SIDEBAR_WIDTH = 200
_SHELL_TIMEOUT_S = 5


def inherit_login_path() -> None:
    """Take PATH from the user's login shell when launched from the app menu, not a terminal.

    A desktop launch gets the session PATH, which misses what shell rc files add (nvm's node,
    OmniRoute), so the claude-CLI route looks absent; a terminal launch already has it.
    """
    shell = os.environ.get("SHELL")
    if sys.platform == "win32" or not shell or (sys.stdin is not None and sys.stdin.isatty()):
        return
    try:
        out = subprocess.run(
            [shell, "-ilc", 'printf "\\n%s" "$PATH"'],
            capture_output=True,
            text=True,
            timeout=_SHELL_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Login shell PATH not read (%r); keeping the session PATH", exc)
        return
    if path := out.strip().rsplit("\n", 1)[-1]:  # the last line: rc files may print banners
        os.environ["PATH"] = path


class MainWindow(QMainWindow):
    """The app window: a sidebar of screens next to the current one."""

    def __init__(self, runner: CoreRunner) -> None:
        super().__init__()
        self.setWindowTitle(_TITLE)
        self.setMinimumSize(*_MIN_SIZE)
        self.runner = runner
        self.search = SearchScreen(runner)
        self.report = ReportScreen()
        self.preferences = PreferencesScreen(runner)
        self.history = HistoryScreen(runner)
        self.settings = SettingsScreen(runner)
        self.settings.checks_ready.connect(self._apply_checks)
        self.search.report_ready.connect(self._show_report)
        self.preferences.saved.connect(self.search.apply_prefs)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(_SIDEBAR_WIDTH)
        # A long or bold item must elide, never grow a scroll bar under the navigation.
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.stack = QStackedWidget()
        screens = (
            ("Поиск", self.search),
            ("Отчёт", self.report),
            ("Настройки поиска", self.preferences),
            ("История цен", self.history),
            ("Состояние и ключи", self.settings),
        )
        for title, screen in screens:
            self.sidebar.addItem(title)
            self.stack.addWidget(screen)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self._load_prefs()
        self.settings.recheck()

    def _apply_checks(self, checks: list[Check]) -> None:
        self.search.set_unavailable(unavailable_sources(checks))
        store_off = not store_available(checks)
        self.preferences.store_warning.setVisible(store_off)
        self.history.store_warning.setVisible(store_off)

    def _load_prefs(self) -> None:
        job = self.runner.submit(prefs.get_all())
        job.finished.connect(self._apply_prefs)
        # Without its store the app still searches; the failure is already in the log.
        job.failed.connect(lambda _msg: logger.warning("Preferences not loaded"))

    def _apply_prefs(self, stored: Mapping[str, Any]) -> None:
        if not stored:  # nothing saved, or the store is off: keep the form's defaults
            return
        self.search.apply_prefs(stored)
        self.preferences.apply_prefs(stored)

    def _show_report(self, result: RecommendResult, run: SearchRun) -> None:
        self.report.show_result(result, run)
        self.sidebar.setCurrentRow(self.stack.indexOf(self.report))


def install_excepthook(parent: QWidget) -> Callable[..., Any]:
    """Show an unexpected error as a dialog instead of letting it end the app; returns the old hook.

    The traceback goes to the log file; the dialog says nothing about internals.
    """
    previous = sys.excepthook
    showing = False

    def show_error(
        exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc, tb)
            return
        nonlocal showing
        logger.critical("Unexpected error in the desktop app", exc_info=(exc_type, exc, tb))
        if showing:  # the dialog's own event loop hit another error: log it, do not stack dialogs
            return
        showing = True
        try:
            QMessageBox.critical(parent, _TITLE, USER_ERROR)
        finally:
            showing = False

    sys.excepthook = show_error
    return previous


def run(argv: list[str] | None = None) -> int:
    """Show the main window and run the Qt event loop until it closes; returns the exit code."""
    app = cast(
        QApplication,
        QApplication.instance() or QApplication(argv if argv is not None else sys.argv),
    )
    app.setDesktopFileName(DESKTOP_ENTRY)
    app.setWindowIcon(QIcon(str(ICON)))
    apply_theme(app)
    runner = CoreRunner()
    window = MainWindow(runner)
    previous_hook = install_excepthook(window)
    window.show()
    try:
        return app.exec()
    finally:
        sys.excepthook = previous_hook
        runner.shutdown()


def main() -> int:
    """``buysmarter-gui``: the same start-up as the CLI (logging, local store), then the window."""
    setup_logging()
    new_run_id()
    inherit_login_path()
    local_store.prepare()
    return run()
