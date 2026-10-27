"""«Категории»: the learned categories, their knowledge and switches, as ``buysmarter category``.

The list and the summary are the CLI's data (``cli/category.category_rows`` and
``category_detail``, the tables from ``cli/report``). Learning goes through the window's shared
``CategoryLearning``, so a learn started here shows on a report of that category and the other
way round; a learn by hand is not counted in the hourly limit, as ``learn-category`` (В8).
"""

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from buysmarter.cli.category import (
    HEADERS,
    NOTES_TOO_LONG,
    CategoryDetail,
    CategoryRow,
    category_detail,
)
from buysmarter.cli.category import category_rows as stored_rows
from buysmarter.cli.report import (
    SUBSTANCE_HEADERS,
    SUBSTANCES_TITLE,
    TIER_HEADERS,
    knowledge_line,
    substance_cells,
    tier_cells,
)
from buysmarter.gui.bridge import CoreRunner
from buysmarter.gui.capability_toggles import CapabilityToggles
from buysmarter.gui.learning import LEARN_FAILED, CategoryLearning
from buysmarter.gui.tables import FitTableWidget, stretch_column
from buysmarter.gui.theme import SPACE, card_body, set_state
from buysmarter.knowledge.generate import learn_category, learn_from_notes
from buysmarter.llm.adapter import LlmError

logger = logging.getLogger(__name__)

LEARN_TEXT = "Изучить"
RELEARN_TEXT = "Переучить"
NOTES_TEXT = "Из заметок…"
WEB_TEXT = "С поиском в интернете"
LEARN_HINT = "AI, 1–3 минуты, платно. Ваши переключатели сохраняются."
EMPTY = "Изученных категорий пока нет: введите категорию и нажмите «Изучить»."
NO_NAME = "Введите категорию, например: робот-пылесос."
RUNNING = "Изучаю категорию «{name}»…"
LEARNED = "Категория «{name}» изучена."
_NOTES_FILTER = "Заметки (*.md *.txt)"

type PickFile = Callable[[QWidget], str]


def pick_notes(parent: QWidget) -> str:
    """The notes file the user picks, ``""`` when cancelled (as ``learn FILE``)."""
    path, _ = QFileDialog.getOpenFileName(parent, "Заметки о категории", "", _NOTES_FILTER)
    return path


def _table(headers: tuple[str, ...], rows: list[list[str]], stretch: int) -> FitTableWidget:
    table = FitTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    for r, cells in enumerate(rows):
        for c, text in enumerate(cells):
            table.setItem(r, c, QTableWidgetItem(text))
    stretch_column(table, stretch)
    return table


async def _from_notes(path: Path, name: str) -> bool:
    """``learn_from_notes``; ``False`` when the LLM answer was cut off, as ``learn`` says."""
    try:
        await learn_from_notes(path, name)
    except LlmError as exc:
        logger.warning("Learning %s from %s cut off: %r", name, path, exc)
        return False
    return True


class CategoriesScreen(QWidget):
    """The stored categories; the picked one's summary and switches; learning by hand."""

    def __init__(
        self, runner: CoreRunner, learning: CategoryLearning, pick_file: PickFile = pick_notes
    ) -> None:
        super().__init__()
        self._runner = runner
        self.learning = learning
        self._pick_file = pick_file
        self._names: set[str] = set()
        self._shown: str | None = None  # the category whose detail is on screen or coming
        self.toggles: CapabilityToggles | None = None
        self.tiers: FitTableWidget | None = None
        self.counts = QLabel(wordWrap=True)
        layout = QVBoxLayout(self)
        # Under the screen heading (app._centered): no top gap of its own.
        layout.setContentsMargins(SPACE["xxl"], SPACE["lg"], SPACE["xxl"], SPACE["xl"])
        layout.setSpacing(SPACE["lg"])
        layout.addWidget(self._build_learn_card())
        layout.addWidget(self._build_list())
        self._detail = QVBoxLayout()
        self._detail.setSpacing(SPACE["lg"])
        layout.addLayout(self._detail)
        layout.addStretch(1)
        learning.started.connect(self._on_learning)
        learning.settled.connect(self._on_learning)

    def _build_learn_card(self) -> QGroupBox:
        self.web = QCheckBox(WEB_TEXT)
        self.web.setChecked(True)
        self.status = QLabel(LEARN_HINT, wordWrap=True)
        set_state(self.status, "role", "caption")
        card = QGroupBox("Изучить категорию")
        inside = card_body(QVBoxLayout(card))
        inside.setSpacing(SPACE["md"])
        inside.addLayout(self._build_learn_row())
        inside.addWidget(self.web)
        inside.addWidget(self.status)
        return card

    def _build_learn_row(self) -> QHBoxLayout:
        """The category field and its two ways to learn."""
        self.name = QLineEdit(placeholderText="например: робот-пылесос")
        self.name.setAccessibleName("Категория")
        self.name.textChanged.connect(self._render_buttons)
        self.learn_button = QPushButton(LEARN_TEXT)
        set_state(self.learn_button, "primary", "true")
        self.learn_button.clicked.connect(self._learn)
        self.notes_button = QPushButton(NOTES_TEXT)
        self.notes_button.clicked.connect(self._learn_notes)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.clicked.connect(lambda: self.learning.cancel(self.name.text().strip()))
        self.cancel_button.hide()
        row = QHBoxLayout()
        row.setSpacing(SPACE["md"])
        row.addWidget(self.name, 1)
        row.addWidget(self.learn_button)
        row.addWidget(self.notes_button)
        row.addWidget(self.cancel_button)
        return row

    def _build_list(self) -> QGroupBox:
        self.table = FitTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)  # wrap, as the other rows
        stretch_column(self.table, 1)
        self.table.itemSelectionChanged.connect(self._picked)
        self.empty = QLabel(wordWrap=True)
        set_state(self.empty, "role", "muted")
        card = QGroupBox("Изученные категории")
        inside = card_body(QVBoxLayout(card))
        inside.addWidget(self.empty)
        inside.addWidget(self.table)
        return card

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        self.reload()

    def reload(self) -> None:
        """Read the stored categories again (on show and after a learn)."""
        job = self._runner.submit(stored_rows())
        job.finished.connect(self._fill)
        job.failed.connect(self.status.setText)  # USER_ERROR: the details are in the log

    def _fill(self, rows: list[CategoryRow]) -> None:
        self._names = {row.name for row in rows}
        self.table.blockSignals(True)  # refilling is not the user picking a row
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                self.table.setItem(r, c, QTableWidgetItem(text))
            if row.name == self._shown:
                self.table.selectRow(r)
        self.table.blockSignals(False)
        self.table.setVisible(bool(rows))
        self.empty.setText("" if rows else EMPTY)
        self.empty.setVisible(not rows)
        self._render_buttons()

    def _picked(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows and (item := self.table.item(rows[0].row(), 0)) is not None:
            self.name.setText(item.text())
            self._load(item.text())

    def _load(self, name: str) -> None:
        self._shown = name
        job = self._runner.submit(category_detail(name))
        job.failed.connect(self.status.setText)
        job.finished.connect(lambda detail: self._show(name, detail))

    def _show(self, name: str, detail: CategoryDetail | None) -> None:
        if name != self._shown:
            return  # another category was picked meanwhile
        self._clear_detail()
        if detail is None:
            return
        self._detail.addWidget(self._knowledge_card(detail))
        self.toggles = CapabilityToggles(self._runner, detail.row.name, detail.states, rerun=False)
        self._detail.addWidget(self.toggles)

    def _clear_detail(self) -> None:
        while (item := self._detail.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.deleteLater()
        self.toggles = self.tiers = None

    def _knowledge_card(self, detail: CategoryDetail) -> QGroupBox:
        row, rows = detail.row, detail.knowledge
        card = QGroupBox(row.name)
        inside = card_body(QVBoxLayout(card))
        inside.setSpacing(SPACE["md"])
        about = QLabel(" · ".join(filter(None, (row.books, row.parent, row.updated))))
        set_state(about, "role", "caption")
        inside.addWidget(about)
        self.tiers = _table(TIER_HEADERS, tier_cells(rows), stretch=2)
        inside.addWidget(self.tiers)
        self.counts = QLabel(knowledge_line(rows), wordWrap=True)
        inside.addWidget(self.counts)
        if rows.substances:
            title = QLabel(SUBSTANCES_TITLE)
            set_state(title, "role", "muted")
            inside.addWidget(title)
            inside.addWidget(_table(SUBSTANCE_HEADERS, substance_cells(rows), stretch=2))
        return card

    def _learn(self) -> None:
        if name := self._named():
            self.learning.run(name, learn_category(name, web_search=self.web.isChecked()))

    def _learn_notes(self) -> None:
        if (name := self._named()) and (path := self._pick_file(self)):
            self.learning.run(name, _from_notes(Path(path), name))

    def _named(self) -> str:
        name = self.name.text().strip()
        if not name:
            self.status.setText(NO_NAME)
        return name

    def _on_learning(self, category: str) -> None:
        if category != self.name.text().strip():
            self._render_buttons()
            return
        outcome = self.learning.outcome(category)
        if self.learning.running(category):
            self.status.setText(RUNNING.format(name=category))
        elif outcome == "":
            self.status.setText(LEARNED.format(name=category))
            self._shown = category
            self.reload()
            self._load(category)
        elif outcome is not None:
            # Only a notes learn cut off by the LLM ends as LEARN_FAILED (``_from_notes``).
            self.status.setText(NOTES_TOO_LONG if outcome == LEARN_FAILED else outcome)
        self._render_buttons()

    def _render_buttons(self) -> None:
        name = self.name.text().strip()
        self.learn_button.setText(RELEARN_TEXT if name in self._names else LEARN_TEXT)
        busy = self.learning.running(name)
        self.learn_button.setEnabled(not busy)
        self.notes_button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
