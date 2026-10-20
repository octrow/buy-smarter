"""Price history screen: recorded snapshots of a product, and a refresh of tracked categories."""

from collections.abc import Callable, Coroutine
from typing import Any

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

from buysmarter.gui.bridge import CoreRunner, Job
from buysmarter.gui.progress import Progress
from buysmarter.gui.screens.report import text_table
from buysmarter.services.price_tracking import CategoryRefresh, ProductHistory
from buysmarter.services.price_tracking import product_history as run_product_history
from buysmarter.services.price_tracking import refresh_tracked as run_refresh_tracked

type FindHistory = Callable[[str], Coroutine[Any, Any, list[ProductHistory]]]
type Refresh = Callable[[], Coroutine[Any, Any, list[CategoryRefresh]]]


def refresh_summary(results: list[CategoryRefresh]) -> str:
    """One line per category, as the CLI's ``refresh`` prints it."""
    if not results:
        return "Отслеживаемых категорий пока нет — сначала выполните поиск."
    return "\n".join(
        f"{r.category}: {r.product_count} products, {r.changed} price change(s)" for r in results
    )


def _history_group(entry: ProductHistory) -> QGroupBox:
    rows = [
        [s.captured_at.replace("T", " "), f"{s.min_price:,} ₸", str(s.offer_count)]
        for s in entry.snapshots
    ]
    box = QGroupBox(f"{entry.title}  [{entry.source}]")
    QVBoxLayout(box).addWidget(text_table(["Date", "Min price", "Offers"], rows))
    return box


class HistoryScreen(QWidget):
    """Look up a product's snapshots; refresh every tracked category with progress and cancel."""

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
        self._progress = Progress()
        self._progress.changed.connect(self._on_progress)

        self.term = QLineEdit(placeholderText="название товара или часть ссылки")
        self.term.returnPressed.connect(self.look_up)
        self.find_button = QPushButton("Показать историю")
        self.find_button.clicked.connect(self.look_up)
        self.refresh_button = QPushButton("Обновить цены")
        self.refresh_button.clicked.connect(self.start_refresh)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_refresh)
        self.status = QLabel(wordWrap=True)
        self.results = QScrollArea(widgetResizable=True)

        search_row = QHBoxLayout()
        search_row.addWidget(self.term, 1)
        search_row.addWidget(self.find_button)
        refresh_row = QHBoxLayout()
        refresh_row.addWidget(self.refresh_button)
        refresh_row.addWidget(self.cancel_button)
        refresh_row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addLayout(refresh_row)
        layout.addWidget(self.status)
        layout.addWidget(self.results, 1)

    def look_up(self) -> None:
        term = self.term.text().strip()
        if not term:
            return
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
        layout = QVBoxLayout(page)
        if not entries:
            layout.addWidget(
                QLabel(f"Нет истории для «{term}» (нет совпадений или хранилище недоступно).")
            )
        for entry in entries:
            layout.addWidget(_history_group(entry))
        layout.addStretch(1)
        self.results.setWidget(page)
        if self._reload_pending:
            self._reload_pending = False
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
