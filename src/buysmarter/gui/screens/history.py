"""Price history screen: recorded snapshots of a product, and a refresh of tracked categories."""

from collections.abc import Callable, Coroutine
from typing import Any

from PySide6.QtCore import QPointF, QSize
from PySide6.QtGui import QPainter, QPen, QPolygonF, QShowEvent
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from buysmarter.cli.history import (
    NO_HISTORY,
    NOTHING_TRACKED,
    RECENT_HEADERS,
    RECENT_TITLE,
    SNAPSHOT_HEADERS,
    history_title,
    recent_cells,
    refresh_summary,
    snapshot_rows,
)
from buysmarter.gui.bridge import CoreRunner, Job
from buysmarter.gui.prefs_fields import store_warning
from buysmarter.gui.progress import Progress
from buysmarter.gui.screens.report import text_table
from buysmarter.gui.theme import SPACE, card_body, set_state
from buysmarter.services.price_tracking import CategoryRefresh, ProductHistory
from buysmarter.services.price_tracking import product_history as run_product_history
from buysmarter.services.price_tracking import refresh_tracked as run_refresh_tracked

type FindHistory = Callable[[str], Coroutine[Any, Any, list[ProductHistory]]]
type Refresh = Callable[[], Coroutine[Any, Any, list[CategoryRefresh]]]


class Sparkline(QWidget):
    """The min price over time, oldest on the left: the trend a table of dates hides."""

    _SIZE = QSize(240, 48)
    _PAD = 4

    def __init__(self, prices: list[int]) -> None:
        super().__init__()
        self._prices = prices
        self.setFixedSize(self._SIZE)

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt's name
        if len(self._prices) < 2:
            return
        low, high = min(self._prices), max(self._prices)
        span = (high - low) or 1
        w, h, pad = self.width() - 2 * self._PAD, self.height() - 2 * self._PAD, self._PAD
        step = w / (len(self._prices) - 1)
        points = [
            QPointF(pad + i * step, pad + h - (p - low) / span * h)
            for i, p in enumerate(self._prices)
        ]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.palette().highlight().color(), 2))
        painter.drawPolyline(QPolygonF(points))


def _history_group(entry: ProductHistory) -> QGroupBox:
    box = QGroupBox(history_title(entry))
    layout = card_body(QVBoxLayout(box))
    prices = [s.min_price for s in reversed(entry.snapshots)]
    if len(prices) > 1:
        layout.addWidget(Sparkline(prices))
    layout.addWidget(text_table(list(SNAPSHOT_HEADERS), snapshot_rows(entry)))
    return box


class HistoryScreen(QWidget):
    """Recent products, a product's snapshots, and a refresh of tracked categories (cancelable).

    An empty search lists the recently tracked products; double-clicking one opens its history.
    """

    def __init__(
        self,
        runner: CoreRunner,
        find: FindHistory = run_product_history,
        refresh: Refresh = run_refresh_tracked,
    ) -> None:
        super().__init__()
        self._runner = runner
        self._find = find
        self._refresh = refresh
        self._refresh_job: Job | None = None
        self._reload_pending = False
        self._shown_once = False
        self._progress = Progress(self)
        self._progress.changed.connect(self._on_progress)

        self.term = QLineEdit(placeholderText="название товара или часть ссылки; пусто — недавние")
        self.term.returnPressed.connect(self.look_up)
        self.find_button = QPushButton("Показать историю")
        self.find_button.clicked.connect(self.look_up)
        set_state(self.find_button, "primary", "true")
        self.refresh_button = QPushButton("Обновить цены")
        self.refresh_button.clicked.connect(self.start_refresh)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_refresh)
        self.status = QLabel(wordWrap=True)
        set_state(self.status, "role", "muted")
        self.status.setText("Заново снимает цены всех категорий, которые вы уже искали.")
        self.store_warning = store_warning()
        self.results = QScrollArea(widgetResizable=True)

        find_box = QGroupBox("Найти товар")
        search_row = card_body(QHBoxLayout(find_box))
        search_row.addWidget(self.term, 1)
        search_row.addWidget(self.find_button)
        track_box = QGroupBox("Отслеживание")
        track = card_body(QVBoxLayout(track_box))
        refresh_row = QHBoxLayout()
        refresh_row.addWidget(self.refresh_button)
        refresh_row.addWidget(self.cancel_button)
        refresh_row.addStretch(1)
        track.addLayout(refresh_row)
        track.addWidget(self.status)
        layout = QVBoxLayout(self)
        # Under the screen heading (app._centered): no top gap of its own.
        layout.setContentsMargins(SPACE["xxl"], SPACE["lg"], SPACE["xxl"], SPACE["xl"])
        layout.setSpacing(SPACE["md"])
        layout.addWidget(self.store_warning)
        layout.addWidget(find_box)
        layout.addWidget(track_box)
        layout.addWidget(self.results, 1)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        if not self._shown_once:  # the first visit opens on the recent products
            self._shown_once = True
            self.look_up()

    def look_up(self) -> None:
        term = self.term.text().strip()
        if not self.find_button.isEnabled():  # a look-up is running: redo it once it is back
            self._reload_pending = True
            return
        self.find_button.setEnabled(False)
        job = self._runner.submit(self._find(term))
        job.finished.connect(lambda entries: self._show_history(term, entries))
        job.failed.connect(self._on_lookup_failed)

    def _show_history(self, term: str, entries: list[ProductHistory]) -> None:
        self.find_button.setEnabled(True)
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        if not entries:
            text = NO_HISTORY.format(term=term) if term else NOTHING_TRACKED
            empty = QLabel(text, wordWrap=True)
            set_state(empty, "role", "muted")  # as «Прошлые поиски» says it has none
            layout.addWidget(empty)
        elif term:
            for entry in entries:
                layout.addWidget(_history_group(entry))
        else:
            layout.addWidget(self._recent_group(entries))
        layout.addStretch(1)
        self.results.setWidget(page)
        if self._reload_pending:
            self._reload_pending = False
            self.look_up()

    def _recent_group(self, entries: list[ProductHistory]) -> QGroupBox:
        rows = [recent_cells(e) for e in entries]
        table = text_table(list(RECENT_HEADERS), rows, stretch=0)
        table.cellDoubleClicked.connect(lambda row, _col: self._open(entries[row].title))
        self.recent = table
        box = QGroupBox(RECENT_TITLE)
        column = card_body(QVBoxLayout(box))
        column.addWidget(table)
        hint = QLabel("Двойной клик — история цены товара.")
        set_state(hint, "role", "caption")
        column.addWidget(hint)
        return box

    def _open(self, title: str) -> None:
        self.term.setText(title)
        self.look_up()

    def _on_lookup_failed(self, message: str) -> None:
        self.find_button.setEnabled(True)
        self.status.setText(message)

    def start_refresh(self) -> None:
        if self._refresh_job is not None:
            return
        self._set_refreshing(True)
        self.status.setText("Обновление цен…")
        self._refresh_job = self._runner.submit(self._progress.track(self._refresh()))
        self._refresh_job.finished.connect(self._on_refreshed)
        self._refresh_job.failed.connect(lambda message: self._end_refresh(message))
        self._refresh_job.cancelled.connect(lambda: self._end_refresh("Обновление отменено."))

    def cancel_refresh(self) -> None:
        if self._refresh_job is not None:
            self.cancel_button.setEnabled(False)
            self._refresh_job.cancel()

    def _on_progress(self, source: str, state: str, stage: str) -> None:
        if state == "running":
            self.status.setText(f"Обновление цен: {stage}…")

    def _on_refreshed(self, results: list[CategoryRefresh]) -> None:
        self._end_refresh(refresh_summary(results))
        self.look_up()  # the new snapshots show up in the open history

    def _end_refresh(self, text: str) -> None:
        self._refresh_job = None
        self._set_refreshing(False)
        self.status.setText(text)

    def _set_refreshing(self, refreshing: bool) -> None:
        self.refresh_button.setEnabled(not refreshing)
        self.cancel_button.setEnabled(refreshing)
