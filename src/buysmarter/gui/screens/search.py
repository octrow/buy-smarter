"""Search screen: the request form, live per-marketplace progress and cancel."""

import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import QElapsedTimer, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
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
    kzt,
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
    "waiting": "в очереди",
    "running": "ищет…",
    "done": "готово",
    "skipped": "пропущен",
    "failed": "ошибка",
}

# The core's stage names (services.recommend / collect) as the user reads them.
STAGE_LABELS = {
    "knowledge resolve": "Уточняю модель товара",
    "query parse": "Разбираю запрос",
    "collect": "Собираю предложения",
    "relevance filter": "Отбираю подходящие",
    "kimovil": "Сверяю характеристики",
    "ranking": "Ранжирую",
    "review summaries": "Свожу отзывы",
}
_STARTING = "Запускаю поиск"
_CANCELLING = "Отменяю"
_USE_LLM_HINT = "Нужен claude CLI (экран «Состояние и ключи»); с ним поиск идёт дольше."
_START_TEXT = "Найти"
_RUNNING_TEXT = "Ищу…"

logger = logging.getLogger(__name__)

_SOURCE_COLUMN_WIDTH = 96
_TICK_MS = 1000


def stage_label(stage: str) -> str:
    """A core stage name in Russian; ``collect[kaspi]`` reads as ``collect``."""
    return STAGE_LABELS.get(stage.split("[", 1)[0], "Работаю")


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
    open_report = Signal()

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
        self._stage_text = ""
        self._elapsed = QElapsedTimer()
        self._ticker = QTimer(self, interval=_TICK_MS)
        self._ticker.timeout.connect(self._show_stage)
        self._build()

    def _build(self) -> None:
        self.query = QLineEdit(placeholderText=_QUERY_PLACEHOLDER)
        self.query.returnPressed.connect(self.start)
        self.city = QLineEdit(placeholderText="из запроса или настроек")
        self.budget = budget_box()
        self.use_llm = checked_box("разбор запроса и сводка отзывов")
        self.use_llm.setToolTip(_USE_LLM_HINT)
        self.sources = source_boxes()
        self.status = {name: QLabel() for name in ALL_SOURCES}
        self.busy = QProgressBar(minimum=0, maximum=0, textVisible=False)  # 0..0: indeterminate
        self.busy.hide()
        self.stage = QLabel()
        set_state(self.stage, "state", "running")
        self.message = QLabel(wordWrap=True)
        set_state(self.message, "role", "muted")
        self.start_button = QPushButton(_START_TEXT, default=True)
        set_state(self.start_button, "primary", "true")
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.report_button = QPushButton("Открыть отчёт")
        self.report_button.clicked.connect(self.open_report.emit)
        self.report_button.hide()

        # A grid, not a QFormLayout: its labels sit at the top of a tall row on some styles.
        form = QGridLayout()
        form.setVerticalSpacing(SPACE["md"])
        form.setHorizontalSpacing(SPACE["lg"])
        form.setColumnStretch(1, 1)
        rows: list[tuple[str, QWidget | QHBoxLayout]] = [
            ("Запрос", self.query),
            ("Город", self.city),
            ("Бюджет", self.budget),
            ("AI", self.use_llm),
        ]
        for i, name in enumerate(ALL_SOURCES):
            row = QHBoxLayout()
            row.setSpacing(SPACE["lg"])
            self.sources[name].setMinimumWidth(_SOURCE_COLUMN_WIDTH)  # statuses line up
            row.addWidget(self.sources[name])
            row.addWidget(self.status[name], 1)
            rows.append(("Маркетплейсы" if i == 0 else "", row))
        for r, (label, field) in enumerate(rows):
            form.addWidget(
                QLabel(label), r, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            if isinstance(field, QWidget):
                form.addWidget(field, r, 1)
            else:
                form.addLayout(field, r, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.report_button)
        buttons.addStretch(1)
        status = QVBoxLayout()
        status.setSpacing(SPACE["xs"])
        status.addWidget(self.busy)
        status.addWidget(self.stage)
        status.addWidget(self.message)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        layout.setSpacing(SPACE["md"])
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addSpacing(SPACE["sm"])  # the status block reads apart from the buttons
        layout.addLayout(status)
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
            if self._job is not None:  # a running search's statuses stay until it settles
                continue
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
        self.report_button.hide()
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
            nearest = [f"• {c.title} — {kzt(c.price)} [{c.source}]" for c in exc.alternatives]
            return NoRecommendation("\n".join([exc.message(), *nearest]))
        except ValidationError:
            raise  # a bug, not "nothing to recommend": the generic message, details in the log
        except ValueError as exc:  # the core's "nothing to recommend" (the CLI prints it too)
            logger.info("No recommendation for %r", query, exc_info=exc)
            return NoRecommendation(f"Не нашлось подходящих предложений: {exc}")

    def cancel(self) -> None:
        if self._job is not None:
            self.cancel_button.setEnabled(False)
            self._stage_text = _CANCELLING
            self._show_stage()
            self._job.cancel()

    def _on_progress(self, source: str, state: str, stage: str) -> None:
        if state == "running" and self._job is not None and self._stage_text != _CANCELLING:
            self._stage_text = stage_label(stage)
            self._show_stage()
        if source in self._states and self._states[source] != "skipped":
            self._states[source] = "skipped" if state == "cancelled" else state
            self._show_states()

    def _show_stage(self) -> None:
        seconds = self._elapsed.elapsed() // 1000 if self._elapsed.isValid() else 0
        self.stage.setText(f"{self._stage_text}… {seconds} с")

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
        final = {"waiting": "skipped", "running": interrupted}
        self._states = {n: final.get(s, s) for n, s in self._states.items()}
        self._show_states()

    def _on_finished(self, result: RecommendResult | NoRecommendation) -> None:
        self._settle()
        if isinstance(result, NoRecommendation):
            self.message.setText(result.message)
            return
        run = SearchRun(self._run.query, self._run.use_llm, dict(self._states))
        self._show_counts(result)
        self.report_button.show()
        self.report_ready.emit(result, run)

    def _show_counts(self, result: RecommendResult) -> None:
        """What each marketplace brought, next to its status and as one summary line."""
        counts = Counter(s.candidate.source for s in result.recommendation.ranked)
        counts["olx"] += len(result.used_listings)
        for name, label in self.status.items():
            if self._states.get(name) == "done":
                label.setText(f"{STATE_LABELS['done']} · {counts[name]}")
        new = sum(n for name, n in counts.items() if name != "olx")
        line = f"Найдено предложений: {new}"
        if counts["olx"]:
            line += f", б/у на OLX: {counts['olx']}"
        self.message.setText(line + ".")

    def note_saved(self) -> None:
        """Say where the finished search's report went."""
        self.message.setText(f"{self.message.text()} Отчёт сохранён — см. «Прошлые поиски».")

    def _on_failed(self, message: str) -> None:
        self._settle(interrupted="failed")
        self.message.setText(message)

    def _on_cancelled(self) -> None:
        self._settle()
        self.message.setText("Поиск отменён.")

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.start_button.setText(_RUNNING_TEXT if running else _START_TEXT)
        self.busy.setVisible(running)
        if running:
            self._stage_text = _STARTING
            self._elapsed.start()
            self._ticker.start()
            self._show_stage()
        else:
            self._ticker.stop()
            self._elapsed.invalidate()
            self.stage.clear()
        self.cancel_button.setEnabled(running)
        self.use_llm.setEnabled(not running)
        for name, box in self.sources.items():
            box.setEnabled(not running and name not in self._unavailable)
