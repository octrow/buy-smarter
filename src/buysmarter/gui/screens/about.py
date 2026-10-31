"""«О программе»: the version, the update channel, auto-update, and a manual check and update.

The words and the work are ``services/update``'s, shared with the CLI's ``update``.
"""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QProcess, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from buysmarter import __version__
from buysmarter.cli.update import CHANNEL_LABELS
from buysmarter.gui.bridge import CoreRunner, Job
from buysmarter.gui.theme import PAGE_GAP, SPACE, Prose, card_body, set_state
from buysmarter.services import update
from buysmarter.services.update import (
    INSTALL_LABELS,
    RELEASES_URL,
    REPO_URL,
    Install,
    Status,
    UpdateError,
    UpdateSettings,
)

_FIRST_CHECK_MS = 5_000  # after start-up, not during it
_AUTO_EVERY_MS = 6 * 60 * 60 * 1000
_NOT_CHECKED = "Обновления ещё не проверялись."
_CHECKING = "Проверяю GitHub…"
_UPDATING = "Обновляю…"

type Check = Callable[[UpdateSettings, Install], Status]
type Apply = Callable[[UpdateSettings, Install], str]


def _safely(work: Callable[[UpdateSettings, Install], Any], *args: Any) -> Any:
    """The work's result, or the UpdateError's message: it is written for the user."""
    try:
        return work(*args)
    except UpdateError as exc:
        return str(exc)


def _link(title: str, url: str) -> QPushButton:
    """A link in the report's style (``inlineLink``): theme colours in both schemes."""
    button = QPushButton(title)
    set_state(button, "flat", "true")
    button.setObjectName("inlineLink")
    button.setToolTip(url)
    button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
    return button


class AboutScreen(QWidget):
    """Emits ``update_ready(str)`` once an update is taken: it runs after a restart."""

    update_ready = Signal(str)

    def __init__(
        self,
        runner: CoreRunner,
        install: Install | None = None,
        check: Check | None = None,
        apply: Apply | None = None,
    ) -> None:
        super().__init__()
        self._runner = runner
        self._install: Install = install or update.detect_install()
        self._settings = update.load_settings(self._install)
        # Looked up at construction, not import: tests swap the module's functions.
        self._check = check or update.check
        self._apply = apply or update.apply
        self._job: Job | None = None
        self._auto_run = False  # the running check came from the timer: apply what it finds
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(_AUTO_EVERY_MS)
        self._auto_timer.timeout.connect(self.auto_tick)

        layout = QVBoxLayout(self)
        # Under the screen heading (app._centered): no top gap of its own.
        layout.setContentsMargins(SPACE["xxl"], SPACE["lg"], SPACE["xxl"], SPACE["xl"])
        layout.setSpacing(PAGE_GAP)
        layout.addWidget(self._version_group())
        layout.addWidget(self._update_group())
        layout.addStretch(1)

    def _version_group(self) -> QGroupBox:
        self.version_label = Prose(self._version_text(__version__))
        links = QHBoxLayout()
        links.setSpacing(SPACE["lg"])
        for title, url in (("Код на GitHub", REPO_URL), ("Релизы", RELEASES_URL)):
            links.addWidget(_link(title, url))
        links.addStretch(1)
        group = QGroupBox("Версия")
        group_layout = card_body(QVBoxLayout(group))
        group_layout.addWidget(self.version_label)
        group_layout.addLayout(links)
        return group

    def _update_controls(self) -> None:
        self.channel = QComboBox()
        for channel in update.channels_for(self._install):
            self.channel.addItem(CHANNEL_LABELS[channel].capitalize(), channel)
        self.channel.setCurrentIndex(self.channel.findData(self._settings.channel))
        self.channel.setEnabled(self.channel.count() > 1)
        self.channel.currentIndexChanged.connect(self._channel_changed)
        self.auto = QCheckBox("Обновлять автоматически")
        self.auto.setChecked(self._settings.auto)
        self.auto.toggled.connect(self._auto_changed)
        self.check_button = QPushButton("Проверить")
        self.check_button.clicked.connect(self.check_now)
        self.update_button = QPushButton("Обновить")
        set_state(self.update_button, "primary", "true")
        self.update_button.setEnabled(False)
        self.update_button.clicked.connect(self.update_now)
        self.restart_button = QPushButton("Перезапустить")
        self.restart_button.setVisible(False)
        self.restart_button.clicked.connect(self.restart)
        self.status = Prose(_NOT_CHECKED)

    def _update_group(self) -> QGroupBox:
        self._update_controls()
        channel_row = QHBoxLayout()
        channel_row.addWidget(self.channel)
        channel_row.addWidget(self.auto)
        channel_row.addStretch(1)
        buttons = QHBoxLayout()
        for button in (self.check_button, self.update_button, self.restart_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        group = QGroupBox("Обновления")
        group_layout = card_body(QVBoxLayout(group))
        group_layout.addWidget(
            Prose(
                "«Код» берёт каждый новый коммит из ветки main, «Релизы» — только выпущенные "
                "версии. Автообновление проверяет GitHub при запуске и раз в 6 часов."
            )
        )
        group_layout.addLayout(channel_row)
        group_layout.addWidget(self.status)
        group_layout.addLayout(buttons)
        return group

    def _version_text(self, current: str) -> str:
        return f"buy-smarter {current} · {INSTALL_LABELS[self._install]}"

    def start_auto(self) -> None:
        """The app calls it once shown; tests never do, so they stay off the network."""
        QTimer.singleShot(_FIRST_CHECK_MS, self, self.auto_tick)
        self._auto_timer.start()

    def auto_tick(self) -> None:
        # Taken already: until the restart this process reports its old version, and a uv tool
        # install would be reinstalled every tick.
        waits_restart = self.restart_button.isVisibleTo(self)
        if self._settings.auto and self._job is None and not waits_restart:
            self._auto_run = True
            self.check_now()

    def check_now(self) -> None:
        if self._job is not None:
            return
        self._busy(_CHECKING)
        self._submit(self._check, self._checked)

    def update_now(self) -> None:
        if self._job is not None:
            return
        self._busy(_UPDATING)
        self._submit(self._apply, self._updated)

    def restart(self) -> None:
        command = update.restart_command(self._install)
        if self.window().close():  # a search running asks first; a "no" keeps this app
            QProcess.startDetached(command[0], command[1:])

    def _submit(self, work: Callable[..., Any], done: Callable[[Any], None]) -> None:
        # A module function, never a bound method, runs in the pool thread (see settings.py).
        coro = asyncio.to_thread(_safely, work, self._settings, self._install)
        self._job = self._runner.submit(coro)
        self._job.finished.connect(done)
        self._job.failed.connect(self._failed)

    def _busy(self, message: str) -> None:
        self.status.setText(message)
        for widget in (self.check_button, self.update_button, self.channel):
            widget.setEnabled(False)

    def _settled(self) -> None:
        self._job = None
        self.check_button.setEnabled(True)
        self.channel.setEnabled(self.channel.count() > 1)

    def _checked(self, result: Status | str) -> None:
        self._settled()
        auto, self._auto_run = self._auto_run, False
        if isinstance(result, str):
            self.status.setText(result)
            return
        self.version_label.setText(self._version_text(result.current))
        self.status.setText(result.note)
        self.update_button.setEnabled(result.can_apply)
        if auto and result.can_apply:
            self.update_now()

    def _updated(self, message: str) -> None:
        self._settled()
        self.status.setText(message)
        self._settings = update.load_settings(self._install)  # uv tool keeps the commit taken
        if message.startswith("Обновлено"):
            self.update_button.setEnabled(False)
            set_state(self.update_button, "primary", "false")
            set_state(self.restart_button, "primary", "true")
            self.restart_button.setVisible(True)
            self.update_ready.emit(message)

    def _failed(self, message: str) -> None:
        self._settled()
        self._auto_run = False
        self.status.setText(message)

    def _channel_changed(self) -> None:
        self._save(replace(self._settings, channel=self.channel.currentData()))
        self.update_button.setEnabled(False)
        self.check_now()

    def _auto_changed(self, on: bool) -> None:
        self._save(replace(self._settings, auto=on))

    def _save(self, settings: UpdateSettings) -> None:
        self._settings = settings
        update.save_settings(settings)
