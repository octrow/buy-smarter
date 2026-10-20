"""Search screen: the request form, live per-marketplace progress and cancel."""

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from buysmarter.gui.bridge import CoreRunner, Job
from buysmarter.gui.prefs_fields import (
    BUDGET,
    CITY,
    EXCLUDED_BRANDS,
    MARKETPLACES,
    as_budget,
    as_list,
    budget_box,
    check_sources,
    checked_box,
    source_boxes,
)
from buysmarter.gui.progress import Progress
from buysmarter.gui.theme import SPACE, set_state
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services.collect import ALL_SOURCES
from buysmarter.services.recommend import NoSubstanceMatch, RecommendResult
from buysmarter.services.recommend import parse_request as run_parse_request
from buysmarter.services.recommend import recommend as run_recommend

_QUERY_PLACEHOLDER = "нужен ирригатор, IPX7, до 40000 тг, Алматы"
STATE_LABELS = {
    "waiting": "ждёт",
    "running": "ищет…",
    "done": "готово",
    "skipped": "пропущен",
    "failed": "ошибка",
}

logger = logging.getLogger(__name__)

_SOURCE_COLUMN_WIDTH = 96

type Recommend = Callable[..., Awaitable[RecommendResult]]
type Parse = Callable[[str, bool], Awaitable[QueryConstraints]]


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

    def __init__(
        self,
        runner: CoreRunner,
        recommend: Recommend = run_recommend,
        parse: Parse = run_parse_request,
    ) -> None:
        super().__init__()
        self._runner = runner
        self._recommend = recommend
        self._parse = parse
        self._excluded_brands: list[str] = []
        self._unavailable: dict[str, str] = {}
        self._job: Job | None = None
        self._states: dict[str, str] = {}
        self._progress = Progress()
        self._progress.changed.connect(self._on_progress)
        self._build()

    def _build(self) -> None:
        self.query = QLineEdit(placeholderText=_QUERY_PLACEHOLDER)
        self.query.returnPressed.connect(self.start)
        self.city = QLineEdit(placeholderText="из запроса или настроек")
        self.budget = budget_box()
        self.use_llm = checked_box("AI: разбор запроса и сводка отзывов")
        self.sources = source_boxes()
        self.status = {name: QLabel() for name in ALL_SOURCES}
        self.stage = QLabel()
        self.message = QLabel(wordWrap=True)
        set_state(self.message, "role", "muted")
        self.start_button = QPushButton("Найти", default=True)
        set_state(self.start_button, "primary", "true")
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
            row.setSpacing(SPACE["lg"])
            self.sources[name].setMinimumWidth(_SOURCE_COLUMN_WIDTH)  # statuses line up
            row.addWidget(self.sources[name])
            row.addWidget(self.status[name], 1)
            form.addRow("", row)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        layout.setSpacing(SPACE["md"])
        form.setVerticalSpacing(SPACE["md"])
        form.setHorizontalSpacing(SPACE["lg"])
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.stage)
        layout.addWidget(self.message)
        layout.addStretch(1)

    def apply_prefs(self, prefs: Mapping[str, Any]) -> None:
        """Pre-fill from saved preferences; excluded brands apply to every search."""
        # A city the user typed wins over a default arriving later.
        if isinstance(city := prefs.get(CITY), str) and not self.city.isModified():
            self.city.setText(city)
        self.budget.setValue(as_budget(prefs.get(BUDGET)))
        check_sources(self.sources, prefs.get(MARKETPLACES))
        for name in self._unavailable:  # health checks may have landed first
            self.sources[name].setChecked(False)
        self._excluded_brands = as_list(prefs.get(EXCLUDED_BRANDS))

    def set_unavailable(self, reasons: Mapping[str, str]) -> None:
        """Turn marketplaces off (``{name: hint}``, from the health checks); others back on."""
        self._unavailable = dict(reasons)
        for name, box in self.sources.items():
            off = name in self._unavailable
            if off:
                box.setChecked(False)
            box.setEnabled(not off and self._job is None)
            box.setToolTip(self._unavailable.get(name, ""))
            self.status[name].setText("недоступен" if off else "")
            set_state(self.status[name], "state", "missing" if off else "")

    def start(self) -> None:
        if self._job is not None:
            return
        query = self.query.text().strip()
        selected = [
            name
            for name, box in self.sources.items()
            if box.isChecked() and name not in self._unavailable
        ]
        if not query or not selected:
            self.message.setText("Введите запрос и выберите хотя бы один маркетплейс.")
            return
        self._states = {name: "waiting" if name in selected else "skipped" for name in ALL_SOURCES}
        self._show_states()
        self.message.clear()
        self._set_running(True)
        self._run = SearchRun(query, self.use_llm.isChecked(), self._states)
        coro = self._search(
            query,
            city=self.city.text().strip() or None,
            budget=self.budget.value() or None,
            use_llm=self._run.use_llm,
            sources=selected,
            excluded_brands=self._excluded_brands,
        )
        self._job = self._runner.submit(self._progress.track(coro))
        self._job.finished.connect(self._on_finished)
        self._job.failed.connect(self._on_failed)
        self._job.cancelled.connect(self._on_cancelled)

    async def _search(
        self, query: str, *, excluded_brands: list[str], **kwargs: Any
    ) -> RecommendResult | NoRecommendation:
        # Runs on the core loop thread: takes plain values only, never touches a widget.
        try:
            if excluded_brands:
                parsed = await self._parse(query, kwargs["use_llm"])
                merged = list(dict.fromkeys([*parsed.excluded_brands, *excluded_brands]))
                kwargs["parsed"] = parsed.model_copy(update={"excluded_brands": merged})
            return await self._recommend(query, **kwargs)
        except NoSubstanceMatch as exc:
            nearest = [f"• {c.title} — {c.price:,} ₸ [{c.source}]" for c in exc.alternatives]
            return NoRecommendation("\n".join([exc.message(), *nearest]))
        except ValidationError:
            raise  # a bug, not "nothing to recommend": the generic message, details in the log
        except ValueError as exc:  # the core's "nothing to recommend" (the CLI prints it too)
            logger.info("No recommendation for %r", query, exc_info=exc)
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
            self._states[source] = "skipped" if state == "cancelled" else state
            self._show_states()

    def _show_states(self) -> None:
        for name, label in self.status.items():
            state = self._states.get(name, "waiting")
            label.setText(STATE_LABELS[state])
            set_state(label, "state", state)

    def _settle(self, interrupted: str = "skipped") -> None:
        """Back to idle; a marketplace still running ends as ``interrupted``.

        A selected marketplace the pipeline never reached did not run (skipped).
        """
        self._job = None
        self._set_running(False)
        self.stage.clear()
        final = {"waiting": "skipped", "running": interrupted}
        self._states = {n: final.get(s, s) for n, s in self._states.items()}
        self._show_states()

    def _on_finished(self, result: RecommendResult | NoRecommendation) -> None:
        self._settle()
        if isinstance(result, NoRecommendation):
            self.message.setText(result.message)
            return
        run = SearchRun(self._run.query, self._run.use_llm, dict(self._states))
        self.report_ready.emit(result, run)

    def _on_failed(self, message: str) -> None:
        self._settle(interrupted="failed")
        self.message.setText(message)

    def _on_cancelled(self) -> None:
        self._settle()
        self.message.setText("Поиск отменён.")

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.use_llm.setEnabled(not running)
        for name, box in self.sources.items():
            box.setEnabled(not running and name not in self._unavailable)
