"""Screenshots of every sidebar screen in light and dark, for a visual review.

    uv run python scripts/screenshots.py OUT_DIR   ->  OUT_DIR/light/*.png, OUT_DIR/dark/*.png

Runs offscreen on the self-test's temp store, with a copy of the saved reports so «Прошлые
поиски» has content; the user's own files are never written. CI uploads the result.
buysmarter is imported inside the functions: the store and Settings read the environment at import.
"""

import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

_SIZE = (1280, 800)
_SETTLE_S = 0.3
_STARTUP_S = 2.0
_PUMP_MS = 50
_REPORTS = Path(".local/share/buysmarter/reports")  # under ~, see cli/report.py


def main(out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _isolate(Path(tmp))
        _grab_all(out)


def _isolate(tmp: Path) -> None:
    """The self-test's temp store, plus a temp home holding a copy of the real saved reports."""
    import keyring

    from buysmarter.gui import selftest

    selftest._isolate(tmp)
    keyring.set_keyring(selftest._MemoryKeyring())
    real_reports = Path.home() / _REPORTS
    os.environ["HOME"] = str(tmp)
    if real_reports.is_dir():
        shutil.copytree(real_reports, tmp / _REPORTS)


def _grab_all(out: Path) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from buysmarter.config import get_settings
    from buysmarter.db.schema import ensure_schema
    from buysmarter.gui.app import MainWindow
    from buysmarter.gui.bridge import CoreRunner
    from buysmarter.gui.theme import apply_theme
    from buysmarter.services import ozon_browser

    ensure_schema(get_settings().database_url)
    ozon_browser.launches = lambda browser: False  # no real browser probes
    app = QApplication(sys.argv[:1])
    apply_theme(app)
    runner = CoreRunner()
    try:
        window = MainWindow(runner)
        window.resize(*_SIZE)
        window.show()
        _pump(app, runner, _STARTUP_S)
        settle = lambda: _pump(app, runner, _SETTLE_S)  # noqa: E731
        for scheme in (Qt.ColorScheme.Light, Qt.ColorScheme.Dark):
            _force_scheme(scheme)
            _grab_screens(window, settle, out / scheme.name.lower())
        window.close()
    finally:
        runner.shutdown()


def _force_scheme(scheme: object) -> None:
    """Offscreen has no OS scheme to set: force the palette, then announce the change."""
    from PySide6.QtGui import QGuiApplication

    from buysmarter.gui import theme
    from buysmarter.gui.screens import saved

    forced = theme.palette_for(scheme)
    theme.current_palette = saved.current_palette = lambda: forced
    QGuiApplication.styleHints().colorSchemeChanged.emit(scheme)


def _grab_screens(window: object, settle: Callable[[], None], out: Path) -> None:
    from buysmarter.gui.app import _SCREEN_ROLE

    out.mkdir(parents=True, exist_ok=True)
    settle()
    for row in range(window.sidebar.count()):
        item = window.sidebar.item(row)
        if item.data(_SCREEN_ROLE) is None:
            continue  # a group header
        window.sidebar.setCurrentRow(row)
        settle()
        name = f"{row:02d}-{item.text()}.png"
        window.grab().save(str(out / name))
        print(name)
    _grab_clarification(window, settle, out)


def _grab_clarification(window: object, settle: Callable[[], None], out: Path) -> None:
    """The search screen asking back (phase 2): the warning, options and own-answer field."""
    from buysmarter.services.understanding import Clarification

    asked = Clarification(
        kind="open",
        question="Что именно ищем? Выберите вариант или напишите свой.",
        options=["духи", "крем для лица", "плед", "кофемашина"],
        restricted_note="Алкоголь: продажа онлайн и доставка ограничены законом.",
    )
    window.search.query.setText("подарок для мамы до 15000")
    window.search.clarify.ask(asked)
    window._show_screen(window.search)
    settle()
    window.grab().save(str(out / "clarify.png"))
    window.search.clarify.hide()
    print("clarify.png")


def _pump(app: object, runner: object, seconds: float) -> None:
    """Process events for ``seconds``, and longer while background jobs run."""
    from PySide6.QtCore import QEventLoop

    end = time.monotonic() + seconds
    while time.monotonic() < end or runner.busy():
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, _PUMP_MS)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
