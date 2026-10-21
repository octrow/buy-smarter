"""Past searches: the markdown reports the CLI and the app save, newest first."""

import logging
import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from buysmarter.cli.report import saved_reports
from buysmarter.gui.theme import SPACE, set_state

logger = logging.getLogger(__name__)

_PATH_ROLE = Qt.ItemDataRole.UserRole
_LIST_WIDTH = 300
# ``default_report_path``: YYYYMMDD-HHMMSS-<query-slug>.md
_NAME = re.compile(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}-(.*)$")
_EMPTY = "Пока нет сохранённых поисков — отчёт сохраняется после каждого поиска."

type ListReports = Callable[[], list[Path]]


def report_label(path: Path) -> str:
    """``21.10.2026 20:38 — смартфон до 200000 алматы`` from a report's file name."""
    m = _NAME.match(path.stem)
    if m is None:
        return path.stem
    year, month, day, hour, minute, slug = m.groups()
    return f"{day}.{month}.{year} {hour}:{minute} — {slug.replace('-', ' ')}"


def _report_item(path: Path) -> QListWidgetItem:
    label = report_label(path)
    item = QListWidgetItem(label)
    item.setData(_PATH_ROLE, path)
    item.setToolTip(f"{label}\n{path}")
    return item


class SavedScreen(QWidget):
    """A list of saved reports; the selected one is shown as rendered markdown."""

    def __init__(self, list_reports: ListReports = saved_reports) -> None:
        super().__init__()
        self._list_reports = list_reports
        self.reports = QListWidget()
        self.reports.setFixedWidth(_LIST_WIDTH)
        # A long query elides; its full label and file are in the tooltip.
        self.reports.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.reports.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.reports.currentItemChanged.connect(self._show)
        self.view = QTextBrowser(openExternalLinks=True)
        self.empty = QLabel(_EMPTY, wordWrap=True)
        set_state(self.empty, "role", "muted")
        self.folder_button = QPushButton("Открыть папку")
        self.folder_button.clicked.connect(self._open_folder)
        self._lay_out()

    def _lay_out(self) -> None:
        side = QVBoxLayout()
        side.addWidget(self.reports, 1)
        side.addWidget(self.folder_button)
        body = QHBoxLayout()
        body.setSpacing(SPACE["lg"])
        body.addLayout(side)
        body.addWidget(self.view, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        layout.addWidget(self.empty)
        layout.addLayout(body, 1)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        self.reload()  # a search may have saved a report since the last visit

    def reload(self) -> None:
        """Re-read the folder; keep the open report selected when it is still there."""
        current = self.reports.currentItem()
        selected = current.data(_PATH_ROLE) if current else None
        self.reports.clear()
        paths = self._list_reports()
        for path in paths:
            self.reports.addItem(_report_item(path))
        self.empty.setVisible(not paths)
        self.folder_button.setEnabled(bool(paths))
        if paths:
            row = paths.index(selected) if selected in paths else 0
            self.reports.setCurrentRow(row)
        else:
            self.view.clear()

    def _show(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        path: Path = item.data(_PATH_ROLE)
        try:
            self.view.setMarkdown(path.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("Saved report %s not read: %r", path, exc)
            self.view.setPlainText("Не удалось открыть отчёт — подробности в логе.")

    def _open_folder(self) -> None:
        item = self.reports.currentItem()
        if item is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(item.data(_PATH_ROLE).parent)))
