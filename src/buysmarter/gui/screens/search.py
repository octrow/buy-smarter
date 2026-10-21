"""Search screen: the request form, live per-marketplace progress and cancel."""

import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import QElapsedTimer, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from buysmarter.cli.report import saved_reports
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
from buysmarter.gui.screens.saved import ListReports, report_parts
from buysmarter.gui.theme import SPACE, bind_icon, set_state
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

_TICK_MS = 1000
_HERO = "Что ищем?"
_HERO_HINT = "Опишите товар, бюджет и город — сравним Kaspi, Ozon и OLX"
_RECENT_COUNT = 3
_ROW_ICON_PX = 18


def _named[W: QWidget](widget: W, name: str) -> W:
    widget.setObjectName(name)
    return widget


def _muted(label: QLabel) -> QLabel:
    set_state(label, "role", "muted")
    return label


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    set_state(label, "role", "caption")
    return label


def _captioned(text: str, field: QWidget) -> QVBoxLayout:
    """``field`` with a small label above it."""
    column = QVBoxLayout()
    column.setSpacing(SPACE["xs"])
    column.addWidget(_caption(text))
    column.addWidget(field)
    return column


def _clear(layout: QVBoxLayout) -> None:
    while (item := layout.takeAt(0)) is not None:
        if (widget := item.widget()) is not None:
            widget.deleteLater()


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
        list_reports: ListReports = saved_reports,
    ) -> None:
        super().__init__()
        self._runner = runner
        self._recommend = recommend
        self._parse = parse
        self._excluded_brands: list[str] = []
        self._unavailable: dict[str, str] = {}
        self._job: Job | None = None
        self._states: dict[str, str] = {}
        self._progress = Progress(self)
        self._progress.changed.connect(self._on_progress)
        self._stage_text = ""
        self._elapsed = QElapsedTimer()
        self._ticker = QTimer(self, interval=_TICK_MS)
        self._ticker.timeout.connect(self._show_stage)
        self._list_reports = list_reports
        self._query_action: QAction | None = None
        self._build()

    def _build(self) -> None:
        self.query = QLineEdit(placeholderText=_QUERY_PLACEHOLDER)
        self.query.returnPressed.connect(self.start)
        self.city = QLineEdit(placeholderText="из запроса или настроек")
        self.budget = budget_box()
        self.use_llm = checked_box("AI: разбор запроса и отзывы")
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

        self.query.setObjectName("bigQuery")
        bind_icon(self, self._set_query_icon, "mdi6.magnify")
        bind_icon(self, self.start_button.setIcon, "mdi6.magnify", "on_accent")
        self._build_recent_box()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["xxl"], SPACE["xxl"], SPACE["xxl"], SPACE["xl"])
        layout.setSpacing(SPACE["lg"])
        layout.addWidget(_named(QLabel(_HERO), "hero"))
        layout.addWidget(_muted(QLabel(_HERO_HINT)))
        layout.addWidget(self._form_card())
        layout.addSpacing(SPACE["sm"])
        layout.addWidget(self.recent_box)
        layout.addStretch(1)

    def _build_recent_box(self) -> None:
        self.recent_box = QWidget()
        self._recent_rows = QVBoxLayout(self.recent_box)
        self._recent_rows.setContentsMargins(0, 0, 0, 0)
        self._recent_rows.setSpacing(SPACE["sm"])
        self._repeat_buttons: list[QPushButton] = []
        self._recent: list[str] = []
        self.recent_box.hide()  # until there is a saved search to show

    def _form_card(self) -> QFrame:
        """The request: query, city and budget, where to search, then the run controls."""
        card = _named(QFrame(), "card")
        body = QVBoxLayout(card)
        body.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        body.setSpacing(SPACE["lg"])
        body.addWidget(self.query)
        where = QHBoxLayout()
        where.setSpacing(SPACE["md"])
        where.addLayout(_captioned("Город", self.city), 1)
        where.addLayout(_captioned("Бюджет", self.budget), 1)
        body.addLayout(where)
        body.addLayout(self._sources_row())
        body.addLayout(self._run_row())
        return card

    def _sources_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SPACE["xl"])
        row.addWidget(_caption("Где искать"))
        for name in ALL_SOURCES:
            cell = QHBoxLayout()
            cell.setSpacing(SPACE["sm"])
            cell.addWidget(self.sources[name])
            cell.addWidget(self.status[name])
            row.addLayout(cell)
        row.addStretch(1)
        row.addWidget(self.use_llm)
        return row

    def _run_row(self) -> QHBoxLayout:
        """Progress on the left, the buttons on the right; the primary one last."""
        status = QVBoxLayout()
        status.setSpacing(SPACE["xs"])
        status.addWidget(self.busy)
        status.addWidget(self.stage)
        status.addWidget(self.message)
        row = QHBoxLayout()
        row.setSpacing(SPACE["sm"])
        row.addLayout(status, 1)
        row.addWidget(self.cancel_button, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.report_button, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.start_button, alignment=Qt.AlignmentFlag.AlignTop)
        return row

    def _set_query_icon(self, drawn: QIcon) -> None:
        if self._query_action is None:
            self._query_action = self.query.addAction(
                drawn, QLineEdit.ActionPosition.LeadingPosition
            )
        else:
            self._query_action.setIcon(drawn)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override
        self.refresh_recent()
        super().showEvent(event)

    def refresh_recent(self) -> None:
        """The latest distinct saved queries, each with a button that searches it again."""
        seen: dict[str, str] = {}
        for path in self._list_reports():
            when, query = report_parts(path)
            seen.setdefault(query, when)
            if len(seen) == _RECENT_COUNT:
                break
        if list(seen) == self._recent:
            return
        self._recent = list(seen)
        _clear(self._recent_rows)
        self._repeat_buttons = []
        if seen:
            self._recent_rows.addWidget(_named(QLabel("Недавние поиски"), "sectionTitle"))
        for query, when in seen.items():
            self._recent_rows.addWidget(self._recent_row(query, when))
        self.recent_box.setVisible(bool(seen))

    def _recent_row(self, query: str, when: str) -> QFrame:
        row = _named(QFrame(), "recent")
        line = QHBoxLayout(row)
        line.setContentsMargins(SPACE["lg"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        line.setSpacing(SPACE["md"])
        mark = QLabel()
        bind_icon(
            mark,
            lambda drawn: mark.setPixmap(drawn.pixmap(_ROW_ICON_PX, _ROW_ICON_PX)),
            "mdi6.history",
        )
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(QLabel(query))
        text.addWidget(_caption(when))
        again = QPushButton("Повторить")
        set_state(again, "flat", "true")
        bind_icon(again, again.setIcon, "mdi6.refresh", "accent")
        again.clicked.connect(lambda: self._repeat(query))
        self._repeat_buttons.append(again)
        line.addWidget(mark)
        line.addLayout(text, 1)
        line.addWidget(again)
        return row

    def recent_queries(self) -> list[str]:
        return list(self._recent)

    def repeat_buttons(self) -> list[QPushButton]:
        return list(self._repeat_buttons)

    def _repeat(self, query: str) -> None:
        self.query.setText(query)
        self.start()

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

    def is_running(self) -> bool:
        return self._job is not None

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
        """Say where the finished search's report went; it now heads the recent searches."""
        self.message.setText(f"{self.message.text()} Отчёт сохранён — см. «Прошлые поиски».")
        self.refresh_recent()

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
