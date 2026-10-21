"""``--self-test``: check that a build starts, without the user's store, keyring or browsers.

CI runs it against each built desktop app (desktop-packaging). It runs offscreen against a temp
store, migrates it, round-trips a dummy key through an in-memory keyring, imports this OS's
keyring backend, builds the main window, and exits 0; any failure prints a traceback and exits 1.
Nothing from ``buysmarter`` is imported at module level: ``self_test`` sets the environment
the store and Settings read at import.
"""

import importlib
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import keyring
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

_DUMMY_KEY = "sk-selftest-0000"
_STARTUP_TIMEOUT_S = 60
_PUMP_MS = 50
FAILURE_LOG = Path(tempfile.gettempdir()) / "buysmarter-selftest.log"
_KEYRING_BACKEND = {"win32": "keyring.backends.Windows", "darwin": "keyring.backends.macOS"}
_LINUX_KEYRING_BACKEND = "keyring.backends.SecretService"


class _MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self.secrets: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.secrets.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.secrets[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.secrets.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


def self_test() -> int:
    # Windows keeps the open SQLite files locked: leftovers in temp beat a crash after the verdict.
    with tempfile.TemporaryDirectory(prefix="buysmarter-", ignore_cleanup_errors=True) as tmp:
        _isolate(Path(tmp))
        try:
            _check()
        except Exception:  # noqa: BLE001 - every failure is the answer: report it, exit 1
            _report_failure(traceback.format_exc())
            return 1
    print("self-test ok", flush=True)
    return 0


def _report_failure(details: str) -> None:
    if sys.stderr is not None:
        sys.stderr.write(details)
    else:  # the windowed Windows build has no console: CI prints this file instead
        FAILURE_LOG.write_text(details, encoding="utf-8")
    print("self-test FAILED", flush=True)


def _isolate(tmp: Path) -> None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp / 'buysmarter.db'}"
    os.environ["BUYSMARTER_PREFS_FILE"] = str(tmp / "prefs.json")
    os.environ["BUYSMARTER_KNOWLEDGE_DIR"] = str(tmp / "knowledge")
    os.environ["OZON_STATE_PATH"] = str(tmp / "ozon_state.json")


def _check() -> None:
    import asyncio

    from buysmarter.config import get_settings
    from buysmarter.db.schema import ensure_schema, schema_is_current

    url = get_settings().database_url
    ensure_schema(url)
    if not asyncio.run(schema_is_current(url)):
        raise RuntimeError("the store did not migrate to head")
    _check_keyring()
    _check_window()


def _check_keyring() -> None:
    from buysmarter.llm import keys

    importlib.import_module(_KEYRING_BACKEND.get(sys.platform, _LINUX_KEYRING_BACKEND))
    keyring.set_keyring(_MemoryKeyring())
    keys.set_key("openai", _DUMMY_KEY)
    if keys.stored() != ("openai", _DUMMY_KEY):
        raise RuntimeError("the dummy key did not round-trip through the keyring")


def _check_window() -> None:
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication

    from buysmarter.gui.app import MainWindow
    from buysmarter.gui.bridge import CoreRunner
    from buysmarter.gui.theme import apply_theme
    from buysmarter.services import ozon_browser

    # The build is under test, not this machine's browsers: no probe launches one.
    ozon_browser.launches = lambda browser: False  # type: ignore[assignment]
    app = QApplication.instance() or QApplication(sys.argv[:1])
    apply_theme(app)  # type: ignore[arg-type]
    runner = CoreRunner()
    try:
        window = MainWindow(runner)
        window.show()
        # Let the start-up jobs (preferences, health checks) settle: cancelling them at shutdown
        # interrupts a store connection mid-close and logs a traceback.
        deadline = time.monotonic() + _STARTUP_TIMEOUT_S
        while runner.busy():
            if time.monotonic() > deadline:
                raise RuntimeError("the window's start-up jobs did not finish")
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, _PUMP_MS)
        window.close()
    finally:
        runner.shutdown()
