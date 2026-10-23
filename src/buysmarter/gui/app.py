"""Desktop app entry point: the QApplication and the main window."""

import logging
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, cast

from PySide6.QtCore import QSize, QSocketNotifier, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from buysmarter import local_store
from buysmarter.cli.report import DEFAULT_TOP, save_report
from buysmarter.gui.bridge import USER_ERROR, CoreRunner
from buysmarter.gui.learning import CategoryLearning
from buysmarter.gui.screens.history import HistoryScreen
from buysmarter.gui.screens.preferences import PreferencesScreen
from buysmarter.gui.screens.report import ReportScreen
from buysmarter.gui.screens.saved import SavedScreen
from buysmarter.gui.screens.search import SearchRun, SearchScreen
from buysmarter.gui.screens.settings import SettingsScreen, store_available, unavailable_sources
from buysmarter.gui.theme import (
    CONTENT_MAX_WIDTH,
    FONT_FAMILY,
    ICON,
    SPACE,
    TYPE,
    apply_theme,
    icon,
)
from buysmarter.logsetup import new_run_id, setup_logging
from buysmarter.memory import prefs
from buysmarter.services.doctor import Check
from buysmarter.services.recommend import RecommendResult

logger = logging.getLogger(__name__)

_TITLE = "buy-smarter"
DESKTOP_ENTRY = "buysmarter"  # packaging/linux/buysmarter.desktop
_MIN_SIZE = (960, 640)
_SIDEBAR_WIDTH = 220
_NAV_ICON_PX = 18
_LOGO_ICON = "mdi6.cart-check"
_LOGO_PX = 24
_HEADER_TRACKING_PX = 0.6
_SCREEN_ROLE = Qt.ItemDataRole.UserRole  # a nav item's index in the screen stack
_ICON_ROLE = Qt.ItemDataRole.UserRole + 1  # its icon name, to re-draw on a scheme change
_SHELL_TIMEOUT_S = 5
_CLOSE_WHILE_SEARCHING = "Поиск ещё идёт. Закрыть приложение и остановить его?"
_CLOSE_WHILE_LEARNING = "Идёт изучение категории. Закрыть приложение и прервать его?"


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
        self.learning = CategoryLearning(runner)
        self.report = ReportScreen(runner, learning=self.learning)
        self.preferences = PreferencesScreen(runner)
        self.history = HistoryScreen(runner)
        self.saved = SavedScreen()
        self.settings = SettingsScreen(runner)
        self.settings.checks_ready.connect(self._apply_checks)
        self.search.report_ready.connect(self._show_report)
        self.search.open_report.connect(lambda: self._show_screen(self.report))
        self.report.rerun_requested.connect(self._rerun)
        self.preferences.saved.connect(self.search.apply_prefs)

        self.stack = QStackedWidget()
        self.sidebar = QListWidget()
        for header, entries in self._nav_groups():
            self.sidebar.addItem(_nav_header(header))
            for title, icon_name, screen in entries:
                # The report caps its own page, inside its scroll area; the others are capped here.
                heading = None if screen is self.search else title
                page = screen if screen is self.report else _centered(screen, heading)
                index = self.stack.addWidget(page)
                self.sidebar.addItem(_nav_item(title, icon_name, index))
        self.sidebar.currentItemChanged.connect(self._open_nav_item)
        self._show_screen(self.search)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._paint_nav_icons)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(_side_panel(self.sidebar))
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self._load_prefs()
        self.settings.recheck()

    def _nav_groups(self) -> tuple[tuple[str, tuple[tuple[str, str, QWidget], ...]], ...]:
        """The sidebar: (group, ((title, icon, screen), ...)), in display order."""
        return (
            (
                "Покупки",
                (
                    ("Поиск", "mdi6.magnify", self.search),
                    ("Отчёт", "mdi6.file-document-outline", self.report),
                    ("Прошлые поиски", "mdi6.history", self.saved),
                    ("История цен", "mdi6.chart-line", self.history),
                ),
            ),
            (
                "Сервис",
                (
                    ("Настройки поиска", "mdi6.tune-variant", self.preferences),
                    ("Состояние и ключи", "mdi6.key-outline", self.settings),
                ),
            ),
        )

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
        self._save_report(result, run.query)
        self._show_screen(self.report)

    def _save_report(self, result: RecommendResult, query: str) -> None:
        """The same markdown report the CLI's ``search`` saves, in the same folder."""
        try:
            path = save_report(result, query, DEFAULT_TOP, None)
        except OSError as exc:  # a full disk must not lose the report on screen
            logger.warning("Report not saved: %r", exc)
            return
        logger.info("Report saved to %s", path)
        self.search.note_saved()

    def _show_screen(self, screen: QWidget) -> None:
        index = next(
            i
            for i in range(self.stack.count())
            if (page := self.stack.widget(i)) is not None
            and (page is screen or page.isAncestorOf(screen))
        )
        for row in range(self.sidebar.count()):
            if self.sidebar.item(row).data(_SCREEN_ROLE) == index:
                self.sidebar.setCurrentRow(row)
                return

    def _open_nav_item(self, item: QListWidgetItem | None) -> None:
        if item is not None:
            self.stack.setCurrentIndex(item.data(_SCREEN_ROLE))

    def _paint_nav_icons(self) -> None:
        """Re-draw the nav icons in the new scheme's colors (the QSS re-applies on its own)."""
        for row in range(self.sidebar.count()):
            item = self.sidebar.item(row)
            if name := item.data(_ICON_ROLE):
                item.setIcon(icon(name))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        """Ask before closing over a running search or learning; ``run`` then cancels it."""
        question = (
            _CLOSE_WHILE_SEARCHING
            if self.search.is_running()
            else _CLOSE_WHILE_LEARNING
            if self.learning.any_running()
            else None
        )
        if question is not None:
            answer = QMessageBox.question(
                self,
                _TITLE,
                question,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()

    def _rerun(self) -> None:
        """Search again as the last search ran: the form, its answer and round (after learning)."""
        self._show_screen(self.search)
        self.search.rerun()


def _nav_header(title: str) -> QListWidgetItem:
    item = QListWidgetItem(title.upper())
    item.setFlags(Qt.ItemFlag.NoItemFlags)  # a group label: neither selectable nor focusable
    font = QFont(FONT_FAMILY)  # the QSS cannot size one item of a list
    font.setPixelSize(TYPE["caption"])
    font.setWeight(QFont.Weight.DemiBold)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, _HEADER_TRACKING_PX)
    item.setFont(font)
    return item


def _nav_item(title: str, icon_name: str, screen_index: int) -> QListWidgetItem:
    item = QListWidgetItem(icon(icon_name), title)
    item.setData(_SCREEN_ROLE, screen_index)
    item.setData(_ICON_ROLE, icon_name)
    return item


def _side_panel(nav: QListWidget) -> QFrame:
    """The app's name above the navigation, on the sidebar tone."""
    nav.setObjectName("sidebar")
    nav.setIconSize(QSize(_NAV_ICON_PX, _NAV_ICON_PX))
    # A long or bold item must elide, never grow a scroll bar under the navigation.
    nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    nav.setTextElideMode(Qt.TextElideMode.ElideRight)
    panel = QFrame()
    panel.setObjectName("side")
    panel.setFixedWidth(_SIDEBAR_WIDTH)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, SPACE["lg"], 0, SPACE["lg"])
    layout.setSpacing(0)
    layout.addLayout(_brand_row())
    layout.addWidget(nav, 1)
    return panel


def _brand_row() -> QHBoxLayout:
    logo = QLabel()
    logo.setPixmap(icon(_LOGO_ICON, "accent").pixmap(_LOGO_PX, _LOGO_PX))
    row = QHBoxLayout()
    row.setContentsMargins(SPACE["lg"], 0, SPACE["lg"], SPACE["sm"])
    row.setSpacing(SPACE["sm"])
    row.addWidget(logo)
    row.addWidget(_named(QLabel(_TITLE), "brand"), 1)
    return row


def _named[W: QWidget](widget: W, name: str) -> W:
    widget.setObjectName(name)
    return widget


def _centered(screen: QWidget, heading: str | None) -> QWidget:
    """``screen`` under its heading, no wider than the content cap, centered in the room left.

    The search screen brings its own heading (``None``); the others take the nav title.
    """
    column = QWidget()
    column.setMaximumWidth(CONTENT_MAX_WIDTH)
    stack = QVBoxLayout(column)
    stack.setContentsMargins(0, 0, 0, 0)
    stack.setSpacing(0)
    if heading is not None:
        title = _named(QLabel(heading), "hero")
        title.setContentsMargins(SPACE["xxl"], SPACE["xxl"], SPACE["xxl"], 0)
        stack.addWidget(title)
    stack.addWidget(screen, 1)
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.addStretch(0)
    row.addWidget(column, 1)
    row.addStretch(0)
    return holder


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


# Python runs signal handlers only between bytecodes; Qt's C++ loop must hand control back.
_SIGNAL_POLL_MS = 200


def quit_from_terminal(app: QApplication) -> Callable[[], None]:
    """Ctrl+C (and Ctrl+D on a terminal stdin) quit the app; returns the undo.

    A second Ctrl+C during a slow shutdown falls through to the default handler and kills it.
    """

    def on_sigint(*_: object) -> None:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        app.quit()

    previous = signal.signal(signal.SIGINT, on_sigint)
    ticker = QTimer(app)
    ticker.timeout.connect(lambda: None)
    ticker.start(_SIGNAL_POLL_MS)
    helpers: list[QTimer | QSocketNotifier] = [ticker]
    if sys.stdin is not None and sys.stdin.isatty():  # a desktop launch has no terminal to read
        fd = sys.stdin.fileno()
        notifier = QSocketNotifier(fd, QSocketNotifier.Type.Read, app)

        def on_stdin() -> None:
            if not os.read(fd, 1024):  # Ctrl+D on an empty line: EOF; typed lines are ignored
                notifier.setEnabled(False)
                app.quit()

        notifier.activated.connect(on_stdin)
        helpers.append(notifier)

    def undo() -> None:
        signal.signal(signal.SIGINT, previous)
        ticker.stop()
        for helper in helpers:
            helper.deleteLater()

    return undo


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
    restore_sigint = quit_from_terminal(app)
    window.show()
    try:
        return app.exec()
    finally:
        restore_sigint()
        sys.excepthook = previous_hook
        runner.shutdown()


def main() -> int:
    """``buysmarter-gui``: the same start-up as the CLI (logging, local store), then the window."""
    setup_logging()
    new_run_id()
    inherit_login_path()
    local_store.prepare()
    return run()
