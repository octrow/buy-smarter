"""Settings screen: the ``doctor`` health checks, the LLM key, and the visible Ozon check."""

import asyncio
import threading
from collections.abc import Callable, Coroutine, Mapping
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from buysmarter.gui.bridge import CoreRunner, Job
from buysmarter.gui.theme import SPACE, card_body, set_state
from buysmarter.llm import keys
from buysmarter.services import doctor
from buysmarter.services.doctor import OZON_BROWSER, STORAGE, STYLE, Check

_CHECKS_LOCK = threading.Lock()
CHECK_LABELS = {
    STORAGE: "Хранилище",
    "LLM": "AI",
    OZON_BROWSER: "Браузер Ozon",
    "Ozon session": "Сессия Ozon",
    STYLE: "Стиль одежды",
}

type OzonCheck = Callable[[], Coroutine[Any, Any, str]]


async def _save_key(provider: str, key: str) -> str:
    """Store the key off the loop (a keyring call may block); the message to show."""
    try:
        await asyncio.to_thread(keys.set_key, provider, key)
    except (keys.KeyStoreError, ValueError) as exc:
        return str(exc)  # names the env var to use instead; never the key
    return f"Ключ {provider} сохранён в системном хранилище."


async def _clear_key(provider: str) -> str:
    try:
        await asyncio.to_thread(keys.clear_key, provider)
    except keys.KeyStoreError as exc:
        return str(exc)
    return f"Ключ {provider} удалён."


def unavailable_sources(checks: list[Check]) -> dict[str, str]:
    """Marketplaces a failing check turns off, with that check's hint."""
    browser = next((c for c in checks if c.name == OZON_BROWSER), None)
    return {"ozon": _hint(browser)} if browser is not None and not browser.ok else {}


def _hint(check: Check) -> str:
    return check.app_hint or check.hint


def store_available(checks: list[Check]) -> bool:
    """False only when the storage check ran and failed."""
    return all(c.ok for c in checks if c.name == STORAGE)


class SettingsScreen(QWidget):
    """Emits ``checks_ready(list[Check])`` after every health check run."""

    checks_ready = Signal(list)

    def __init__(
        self,
        runner: CoreRunner,
        checks: Callable[[], list[Check]] | None = None,
        ozon_check: OzonCheck | None = None,
    ) -> None:
        super().__init__()
        self._runner = runner
        # Looked up at construction, not import: tests swap the module's functions.
        self._checks = checks or doctor.doctor_checks
        self._ozon_check = ozon_check or doctor.pass_ozon_check
        self._check_job: Job | None = None
        self._ozon_job: Job | None = None
        self._key_job: Job | None = None
        self._recheck_pending = False  # asked while a check ran (e.g. a key saved during it)
        self._row_labels: dict[str, tuple[QLabel, ...]] = {}

        self.rows = QGridLayout()
        self.rows.setHorizontalSpacing(SPACE["lg"])
        self.rows.setVerticalSpacing(SPACE["sm"])
        self.recheck_button = QPushButton("Проверить снова")
        self.recheck_button.clicked.connect(self.recheck)
        health = QGroupBox("Состояние")
        health_layout = card_body(QVBoxLayout(health))
        health_layout.addLayout(self.rows)
        recheck_row = QHBoxLayout()
        recheck_row.addWidget(self.recheck_button)
        recheck_row.addStretch(1)
        health_layout.addLayout(recheck_row)

        self.ozon_button = QPushButton("Пройти проверку Ozon")
        self.ozon_button.clicked.connect(self.pass_ozon)
        self.ozon_message = QLabel(
            "Откроется окно браузера: дождитесь результатов поиска Ozon, сессия сохранится.",
            wordWrap=True,
        )
        ozon = QGroupBox("Ozon")
        ozon_layout = card_body(QVBoxLayout(ozon))
        ozon_layout.addWidget(self.ozon_message)
        button_row = QHBoxLayout()
        button_row.addWidget(self.ozon_button)
        button_row.addStretch(1)
        ozon_layout.addLayout(button_row)

        layout = QVBoxLayout(self)
        # Under the screen heading (app._centered): no top gap of its own.
        layout.setContentsMargins(SPACE["xxl"], SPACE["lg"], SPACE["xxl"], SPACE["xl"])
        layout.setSpacing(SPACE["lg"])  # between the cards, as on «Категории» and «Стиль»
        layout.addWidget(health)
        layout.addWidget(self._key_group())
        layout.addWidget(ozon)
        layout.addStretch(1)

    def _key_group(self) -> QGroupBox:
        self.provider = QComboBox()
        self.provider.addItems(list(keys.KEY_FIELDS))
        self.key_input = QLineEdit(placeholderText="API-ключ")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.save_button = QPushButton("Сохранить")
        set_state(self.save_button, "primary", "true")
        self.save_button.clicked.connect(self.save_key)
        self.clear_button = QPushButton("Удалить ключ")
        self.clear_button.clicked.connect(self.clear_key)
        self.key_message = QLabel(
            "Нужен, только если AI работает не через claude CLI. Ключ хранится в системном "
            "хранилище паролей и больше не показывается.",
            wordWrap=True,
        )
        row = QHBoxLayout()
        for widget in (self.provider, self.key_input, self.save_button, self.clear_button):
            row.addWidget(widget)
        row.setStretch(1, 1)
        group = QGroupBox("AI-ключ")
        group_layout = card_body(QVBoxLayout(group))
        group_layout.addWidget(self.key_message)
        group_layout.addLayout(row)
        return group

    def save_key(self) -> None:
        key = self.key_input.text().strip()
        if not key:
            self.key_message.setText("Введите ключ.")
            return
        self.key_input.clear()  # the key never stays on screen
        self._run_key_job(_save_key(self.provider.currentText(), key))

    def clear_key(self) -> None:
        self._run_key_job(_clear_key(self.provider.currentText()))

    def _run_key_job(self, coro: Coroutine[Any, Any, str]) -> None:
        if self._key_job is not None:
            coro.close()
            return
        self._set_key_busy(True)
        self._key_job = self._runner.submit(coro)
        self._key_job.finished.connect(self._key_done)
        self._key_job.failed.connect(self._key_done)

    def _key_done(self, message: str) -> None:
        self._key_job = None
        self._set_key_busy(False)
        self.key_message.setText(message)
        self.recheck()

    def _set_key_busy(self, busy: bool) -> None:
        for widget in (self.provider, self.key_input, self.save_button, self.clear_button):
            widget.setEnabled(not busy)

    def recheck(self) -> None:
        if self._check_job is not None:
            self._recheck_pending = True
            return
        self.recheck_button.setEnabled(False)
        # doctor_checks is blocking (asyncio.run, Playwright's sync API): run it off the loop.
        self._check_job = self._runner.submit(asyncio.to_thread(self._serialized_checks))
        self._check_job.finished.connect(self._show_checks)
        self._check_job.failed.connect(self._checks_failed)

    def _serialized_checks(self) -> list[Check]:
        # Two Playwright sync instances launching at once in pool threads can crash in greenlet.
        with _CHECKS_LOCK:
            return self._checks()

    def _check_settled(self) -> None:
        self._check_job = None
        self.recheck_button.setEnabled(True)
        if self._recheck_pending:
            self._recheck_pending = False
            self.recheck()

    def _show_checks(self, checks: list[Check]) -> None:
        self._check_settled()
        self._clear_rows()
        for row, check in enumerate(checks):
            status = QLabel("ок" if check.ok else "нет")
            set_state(status, "state", "ok" if check.ok else "missing")
            labels = (
                QLabel(CHECK_LABELS.get(check.name, check.name)),
                status,
                QLabel(
                    (check.app_detail or check.detail) if check.ok else _hint(check), wordWrap=True
                ),
            )
            for col, label in enumerate(labels):
                self.rows.addWidget(label, row, col, Qt.AlignmentFlag.AlignTop)
            self._row_labels[check.name] = labels
        self.rows.setColumnStretch(2, 1)
        self.checks_ready.emit(checks)

    def _clear_rows(self) -> None:
        for labels in self._row_labels.values():
            for label in labels:
                label.deleteLater()
        self._row_labels = {}

    def _checks_failed(self, message: str) -> None:
        self._check_settled()
        self._clear_rows()
        error = QLabel(message, wordWrap=True)
        self.rows.addWidget(error, 0, 0, 1, 3)
        self._row_labels = {"": (error,)}

    def row_texts(self) -> Mapping[str, tuple[str, str]]:
        """``{check name: (status, detail or hint)}`` as shown."""
        return {
            name: (labels[1].text(), labels[2].text())
            for name, labels in self._row_labels.items()
            if len(labels) == 3
        }

    def pass_ozon(self) -> None:
        if self._ozon_job is not None:
            return
        self.ozon_button.setEnabled(False)
        self.ozon_message.setText("Окно браузера открыто: дождитесь результатов поиска Ozon…")
        self._ozon_job = self._runner.submit(self._ozon_check())
        self._ozon_job.finished.connect(self._ozon_done)
        self._ozon_job.failed.connect(self._ozon_failed)

    def _ozon_done(self, outcome: str) -> None:
        self._ozon_job = None
        self.ozon_button.setEnabled(True)
        self.ozon_message.setText(doctor.ozon_setup_message(outcome))  # the CLI's words
        self.recheck()

    def _ozon_failed(self, message: str) -> None:
        self._ozon_job = None
        self.ozon_button.setEnabled(True)
        self.ozon_message.setText(message)
