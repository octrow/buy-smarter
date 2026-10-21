"""Desktop app entry point: the QApplication and the main window."""

import logging
import sys
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, cast

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
from buysmarter.gui.screens.settings import SettingsScreen, unavailable_sources
from buysmarter.gui.theme import ICON, apply_theme
from buysmarter.logsetup import new_run_id, setup_logging
from buysmarter.memory import prefs
from buysmarter.services.recommend import RecommendResult

logger = logging.getLogger(__name__)

_TITLE = "buy-smarter"
DESKTOP_ENTRY = "buysmarter"  # packaging/linux/buysmarter.desktop
_MIN_SIZE = (960, 640)
_SIDEBAR_WIDTH = 180


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
        self.settings.checks_ready.connect(
            lambda checks: self.search.set_unavailable(unavailable_sources(checks))
        )
        self.search.report_ready.connect(self._show_report)
        self.preferences.saved.connect(self.search.apply_prefs)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(_SIDEBAR_WIDTH)
        self.stack = QStackedWidget()
        screens = (
            ("Поиск", self.search),
            ("Отчёт", self.report),
            ("Настройки поиска", self.preferences),
            ("История цен", self.history),
            ("Настройки", self.settings),
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
    local_store.prepare()
    return run()
