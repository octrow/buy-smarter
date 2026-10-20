"""Settings screen: the same health checks as ``doctor``, and the visible Ozon check."""

import asyncio
import threading
from collections.abc import Callable, Coroutine, Mapping
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from buysmarter.gui.bridge import CoreRunner, Job
from buysmarter.gui.theme import SPACE, set_state
from buysmarter.services import doctor
from buysmarter.services.doctor import OZON_BROWSER, Check

_CHECKS_LOCK = threading.Lock()

type OzonCheck = Callable[[], Coroutine[Any, Any, bool]]


def unavailable_sources(checks: list[Check]) -> dict[str, str]:
    """Marketplaces a failing check turns off, with that check's hint."""
    browser = next((c for c in checks if c.name == OZON_BROWSER), None)
    return {"ozon": browser.hint} if browser is not None and not browser.ok else {}


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
        self._row_labels: dict[str, tuple[QLabel, ...]] = {}

        self.rows = QGridLayout()
        self.rows.setHorizontalSpacing(SPACE["lg"])
        self.rows.setVerticalSpacing(SPACE["sm"])
        self.recheck_button = QPushButton("Проверить снова")
        self.recheck_button.clicked.connect(self.recheck)
        health = QGroupBox("Состояние")
        health_layout = QVBoxLayout(health)
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
        ozon_layout = QVBoxLayout(ozon)
        ozon_layout.addWidget(self.ozon_message)
        button_row = QHBoxLayout()
        button_row.addWidget(self.ozon_button)
        button_row.addStretch(1)
        ozon_layout.addLayout(button_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["xl"], SPACE["sm"], SPACE["xl"], SPACE["xl"])
        layout.addWidget(health)
        layout.addWidget(ozon)
        layout.addStretch(1)

    def recheck(self) -> None:
        if self._check_job is not None:
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

    def _show_checks(self, checks: list[Check]) -> None:
        self._check_job = None
        self.recheck_button.setEnabled(True)
        self._clear_rows()
        for row, check in enumerate(checks):
            status = QLabel("ок" if check.ok else "нет")
            set_state(status, "state", "ok" if check.ok else "missing")
            labels = (
                QLabel(check.name),
                status,
                QLabel(check.detail if check.ok else check.hint, wordWrap=True),
            )
            for col, label in enumerate(labels):
                self.rows.addWidget(label, row, col)
            self._row_labels[check.name] = labels
        self.rows.setColumnStretch(2, 1)
        self.checks_ready.emit(checks)

    def _clear_rows(self) -> None:
        for labels in self._row_labels.values():
            for label in labels:
                label.deleteLater()
        self._row_labels = {}

    def _checks_failed(self, message: str) -> None:
        self._check_job = None
        self.recheck_button.setEnabled(True)
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

    def _ozon_done(self, saved: bool) -> None:
        self._ozon_job = None
        self.ozon_button.setEnabled(True)
        if saved:
            self.ozon_message.setText("Сессия Ozon сохранена.")
        else:
            self.ozon_message.setText("Не удалось получить сессию Ozon за отведённое время.")
        self.recheck()

    def _ozon_failed(self, message: str) -> None:
        self._ozon_job = None
        self.ozon_button.setEnabled(True)
        self.ozon_message.setText(message)
