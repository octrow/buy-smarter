"""Desktop app entry point: the QApplication and the main window."""

import sys

from PySide6.QtWidgets import QApplication, QMainWindow

from buysmarter import local_store
from buysmarter.logsetup import new_run_id, setup_logging

_TITLE = "buy-smarter"
_MIN_SIZE = (960, 640)


class MainWindow(QMainWindow):
    """The app window; the screens hang off its sidebar."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(_TITLE)
        self.setMinimumSize(*_MIN_SIZE)


def run(argv: list[str] | None = None) -> int:
    """Show the main window and run the Qt event loop until it closes; returns the exit code."""
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


def main() -> int:
    """``buysmarter-gui``: the same start-up as the CLI (logging, local store), then the window."""
    setup_logging()
    new_run_id()
    local_store.prepare()
    return run()
