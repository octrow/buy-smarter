"""Search screen: the request form, live per-marketplace progress and cancel."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from buysmarter.gui.bridge import CoreRunner, Job
from buysmarter.gui.progress import Progress
from buysmarter.services.collect import ALL_SOURCES
from buysmarter.services.recommend import NoSubstanceMatch, RecommendResult
from buysmarter.services.recommend import recommend as run_recommend

_BUDGET_MAX = 100_000_000
_BUDGET_STEP = 1_000
_QUERY_PLACEHOLDER = "нужен ирригатор, IPX7, до 40000 тг, Алматы"
SOURCE_LABELS = {"kaspi": "Kaspi", "ozon": "Ozon", "olx": "OLX"}
STATE_LABELS = {
    "waiting": "ждёт",
    "running": "ищет…",
    "done": "готово",
    "skipped": "пропущен",
    "failed": "ошибка",
}

type Recommend = Callable[..., Awaitable[RecommendResult]]


@dataclass(frozen=True)
class SearchRun:
    """What a finished search asked for and how each marketplace ended (for the report)."""

    query: str
    use_llm: bool
    source_states: Mapping[str, str]


@dataclass(frozen=True)
class NoRecommendation:
    """The core found nothing it may recommend; ``message`` is safe to show as is."""

    message: str


class SearchScreen(QWidget):
    """Emits ``report_ready(RecommendResult, SearchRun)`` when a search finishes."""

    report_ready = Signal(object, object)

    def __init__(self, runner: CoreRunner, recommend: Recommend = run_recommend) -> None:
        super().__init__()
        self._runner = runner
        self._recommend = recommend
        self._job: Job | None = None
        self._states: dict[str, str] = {}
        self._progress = Progress()
        self._progress.changed.connect(self._on_progress)
        self._build()

    def _build(self) -> None:
        self.query = QLineEdit(placeholderText=_QUERY_PLACEHOLDER)
        self.query.returnPressed.connect(self.start)
        self.city = QLineEdit(placeholderText="из запроса или настроек")
        self.budget = QSpinBox(maximum=_BUDGET_MAX, singleStep=_BUDGET_STEP, suffix=" ₸")
        self.budget.setSpecialValueText("без лимита")
        self.budget.setGroupSeparatorShown(True)
        self.use_llm = _checked_box("AI: разбор запроса и сводка отзывов")
        self.sources = {name: _checked_box(SOURCE_LABELS[name]) for name in ALL_SOURCES}
        self.status = {name: QLabel() for name in ALL_SOURCES}
        self.stage = QLabel()
        self.message = QLabel(wordWrap=True)
        self.start_button = QPushButton("Найти", default=True)
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)

        form = QFormLayout()
        form.addRow("Запрос", self.query)
        form.addRow("Город", self.city)
        form.addRow("Бюджет", self.budget)
        form.addRow("", self.use_llm)
        for name in ALL_SOURCES:
            row = QHBoxLayout()
            row.addWidget(self.sources[name])
            row.addWidget(self.status[name], 1)
            form.addRow("", row)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.stage)
        layout.addWidget(self.message)
        layout.addStretch(1)

    def apply_prefs(self, prefs: Mapping[str, Any]) -> None:
        """Pre-fill from saved preferences: ``city``, ``budget``, ``marketplaces``."""
        if isinstance(city := prefs.get("city"), str):
            self.city.setText(city)
        budget = str(prefs.get("budget") or "").replace(" ", "")
        if budget.isdigit():
            self.budget.setValue(min(int(budget), _BUDGET_MAX))
        marketplaces = prefs.get("marketplaces")
        if isinstance(marketplaces, str):
            marketplaces = marketplaces.split(",")
        if isinstance(marketplaces, list):
            wanted = {str(m).strip().lower() for m in marketplaces}
            for name, box in self.sources.items():
                box.setChecked(name in wanted)

    def start(self) -> None:
        if self._job is not None:
            return
        query = self.query.text().strip()
        selected = [name for name, box in self.sources.items() if box.isChecked()]
        if not query or not selected:
            self.message.setText("Введите запрос и выберите хотя бы один маркетплейс.")
            return
        self._states = {name: "waiting" if name in selected else "skipped" for name in ALL_SOURCES}
        self._show_states()
        self.message.clear()
        self._set_running(True)
        self._progress.attach()
        self._run = SearchRun(query, self.use_llm.isChecked(), self._states)
        coro = self._search(
            query,
            city=self.city.text().strip() or None,
            budget=self.budget.value() or None,
            use_llm=self._run.use_llm,
            sources=selected,
        )
        self._job = self._runner.submit(coro)
        self._job.finished.connect(self._on_finished)
        self._job.failed.connect(self._on_failed)
        self._job.cancelled.connect(self._on_cancelled)

    async def _search(self, query: str, **kwargs: Any) -> RecommendResult | NoRecommendation:
        # Runs on the core loop thread: takes plain values only, never touches a widget.
        try:
            return await self._recommend(query, **kwargs)
        except NoSubstanceMatch as exc:
            return NoRecommendation(exc.message())
        except ValueError as exc:  # the core's "nothing to recommend" (the CLI prints it too)
            return NoRecommendation(f"No recommendation: {exc}")

    def cancel(self) -> None:
        if self._job is not None:
            self.cancel_button.setEnabled(False)
            self.stage.setText("Отмена…")
            self._job.cancel()

    def _on_progress(self, source: str, state: str, stage: str) -> None:
        if state == "running":
            self.stage.setText(f"{stage}…")
        if source in self._states and self._states[source] != "skipped":
            self._states[source] = state
            self._show_states()

    def _show_states(self) -> None:
        for name, label in self.status.items():
            label.setText(STATE_LABELS[self._states.get(name, "waiting")])

    def _settle(self) -> None:
        self._progress.detach()
        self._job = None
        self._set_running(False)
        self.stage.clear()
        # A selected marketplace the pipeline never reached did not run.
        self._states = {n: "skipped" if s == "waiting" else s for n, s in self._states.items()}
        self._show_states()

    def _on_finished(self, result: RecommendResult | NoRecommendation) -> None:
        self._settle()
        if isinstance(result, NoRecommendation):
            self.message.setText(result.message)
            return
        run = SearchRun(self._run.query, self._run.use_llm, dict(self._states))
        self.report_ready.emit(result, run)

    def _on_failed(self, message: str) -> None:
        self._settle()
        self.message.setText(message)

    def _on_cancelled(self) -> None:
        self._settle()
        self.message.setText("Поиск отменён.")

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        for box in (*self.sources.values(), self.use_llm):
            box.setEnabled(not running)


def _checked_box(text: str) -> QCheckBox:
    box = QCheckBox(text)
    box.setChecked(True)
    return box
