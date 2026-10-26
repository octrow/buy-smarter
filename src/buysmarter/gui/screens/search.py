"""Search screen: the request form, live per-marketplace progress and cancel."""

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

import httpx
from pydantic import ValidationError
from PySide6.QtCore import QElapsedTimer, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from buysmarter.cli.report import (
    COMPARED_HEADER,
    NO_SUBSTANCE_HEADERS,
    USED_HEADERS,
    found_counts,
    found_line,
    marketplace_error_text,
    no_new_pick_message,
    no_recommendation_text,
    no_substance_cells,
    report_parts,
    saved_reports,
    used_cells,
)
from buysmarter.gui.bridge import CoreRunner, Job
from buysmarter.gui.learning import CategoryLearning
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
from buysmarter.gui.screens.clarify import ClarificationBox
from buysmarter.gui.screens.saved import ListReports
from buysmarter.gui.tables import FitTableWidget, stretch_column
from buysmarter.gui.theme import SPACE, bind_icon, set_state
from buysmarter.i18n import stage_label
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services.auto_learn import NeedsLearning
from buysmarter.services.collect import ALL_SOURCES
from buysmarter.services.recommend import (
    NoNewPick,
    NoSubstanceMatch,
    RecommendResult,
    with_excluded,
)
from buysmarter.services.recommend import parse_request as run_parse_request
from buysmarter.services.recommend import recommend as run_recommend
from buysmarter.services.understanding import Clarification, NeedsClarification, NothingToSearch

_QUERY_PLACEHOLDER = "нужен ирригатор, IPX7, до 40000 тг, Алматы"
# Empty city and budget fields: the query's, else the saved ones (``apply_prefs`` names them).
_CITY_HINT = "из запроса или настроек"
_BUDGET_HINT = "из запроса или без лимита"
STATE_LABELS = {
    "waiting": "в очереди",
    "running": "ищет…",
    "done": "готово",
    "skipped": "пропущен",
    "failed": "ошибка",
}

_STARTING = "Запускаю поиск"
_CANCELLING = "Отменяю"
_LEARNING = "Изучаю категорию «{category}» с AI (1–3 мин, платно)"
_REVIEW_SUMMARY_HINT = "AI сводит отзывы о лучшем выборе; без сводки поиск быстрее."
_TRUSTED_ONLY_HINT = "Отсеять бренды, о которых нет данных."
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


type Recommend = Callable[..., Awaitable[RecommendResult]]
type Parse = Callable[[str, bool], Awaitable[QueryConstraints]]


@dataclass(frozen=True)
class SearchRun:
    """What a finished search asked for and how each marketplace ended (for the report)."""

    query: str
    use_llm: bool
    source_states: Mapping[str, str]
    review_summary: bool = True  # off on the form: the report does not call it missing


@dataclass(frozen=True)
class NoRecommendation:
    """The core found nothing it may recommend; ``message`` is safe to show as is.

    ``headers`` and ``rows`` are the CLI's table under that message (the nearest products
    without the substance, the OLX ads), ``urls`` the offer each row opens.
    """

    message: str
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    urls: tuple[str | None, ...] = ()


def _no_substance(exc: NoSubstanceMatch) -> NoRecommendation:
    """The CLI's table of the nearest products without the substance."""
    rows = tuple(tuple(no_substance_cells(c)) for c in exc.alternatives)
    urls = tuple(c.url for c in exc.alternatives)
    return NoRecommendation(exc.message(), tuple(NO_SUBSTANCE_HEADERS), rows, urls)


def _no_new_pick(exc: NoNewPick) -> NoRecommendation:
    """The CLI's OLX table of the ads within the budget."""
    rows = tuple(tuple(used_cells(u, set())) for u in exc.used_listings)
    urls = tuple(u.url for u in exc.used_listings)
    return NoRecommendation(no_new_pick_message(exc), tuple(USED_HEADERS), rows, urls)


@dataclass(frozen=True)
class LearnFirst:
    """The core stopped to learn a new category before its search (``NeedsLearning``)."""

    category: str


@dataclass(frozen=True)
class AskUser:
    """The core asks back before it searches (``services.understanding``)."""

    clarification: Clarification


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
        learning: CategoryLearning | None = None,
    ) -> None:
        super().__init__()
        self._runner = runner
        self.learning = learning or CategoryLearning(runner)
        self.learning.settled.connect(self._on_learning_settled)
        self._learning_for: str | None = None  # the category this search waits for
        self._learn_note = ""  # a failed learn, said next to the counts
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
        self._asked: Clarification | None = None
        # The answer, round and no_clarify the last search ran with, for ``rerun``.
        self._last_rounds: tuple[str | None, int, bool] = (None, 0, False)
        self._build()

    def _build(self) -> None:
        self.query = QLineEdit(placeholderText=_QUERY_PLACEHOLDER)
        self.query.returnPressed.connect(self.start)
        self.city = QLineEdit(placeholderText=_CITY_HINT)
        self.budget = budget_box()
        self.budget.setSpecialValueText(_BUDGET_HINT)
        self.use_llm = checked_box("AI: разбор запроса")
        self.use_llm.setToolTip(_USE_LLM_HINT)
        self.review_summary = checked_box("Сводка отзывов")
        self.review_summary.setToolTip(_REVIEW_SUMMARY_HINT)
        self.use_llm.toggled.connect(self.review_summary.setEnabled)
        self.trusted_only = QCheckBox("Только известные бренды")
        self.trusted_only.setToolTip(_TRUSTED_ONLY_HINT)
        self.sources = source_boxes()
        self.status = {name: QLabel() for name in ALL_SOURCES}
        self.busy = QProgressBar(minimum=0, maximum=0, textVisible=False)  # 0..0: indeterminate
        self.busy.hide()
        self.stage = QLabel(wordWrap=True)  # a long category name wraps, the timer stays seen
        set_state(self.stage, "state", "running")
        self.message = QLabel(wordWrap=True)
        set_state(self.message, "role", "muted")
        self.start_button = QPushButton(_START_TEXT, default=True)
        set_state(self.start_button, "primary", "true")
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.skip_learning_button = QPushButton("Искать без изучения")
        self.skip_learning_button.setAccessibleName("Искать без изучения категории")
        self.skip_learning_button.clicked.connect(self.skip_learning)
        self.skip_learning_button.hide()
        self.report_button = QPushButton("Открыть отчёт")
        self.report_button.clicked.connect(self.open_report.emit)
        self.report_button.hide()
        self._outcome_urls: tuple[str | None, ...] = ()
        self.outcome_table = FitTableWidget(0, 0)  # the CLI's table under a no-pick message
        self.outcome_table.setToolTip(
            "Двойной клик по строке — открыть предложение на маркетплейсе."
        )
        self.outcome_table.cellDoubleClicked.connect(self._open_outcome_row)
        self.outcome_table.hide()
        self.clarify = ClarificationBox()
        self.clarify.answered.connect(self._answer)
        self.clarify.skipped.connect(self._skip_questions)

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
        layout.addWidget(self.clarify)
        layout.addWidget(self.outcome_table)
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
        body.addLayout(self._options_row())
        body.addLayout(self._run_row())
        return card

    def _sources_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SPACE["lg"])  # as the options row below: the boxes line up
        row.addWidget(_caption("Где искать"))
        for name in ALL_SOURCES:
            cell = QHBoxLayout()
            cell.setSpacing(SPACE["sm"])
            cell.addWidget(self.sources[name])
            cell.addWidget(self.status[name])
            row.addLayout(cell)
        row.addStretch(1)
        return row

    def _options_row(self) -> QHBoxLayout:
        """How to search, the CLI's --no-llm, --no-review-summary and --trusted-only."""
        row = QHBoxLayout()
        row.setSpacing(SPACE["lg"])  # all three fit at the window's minimum width
        row.addWidget(_caption("Как искать"))
        for box in (self.use_llm, self.review_summary, self.trusted_only):
            row.addWidget(box)
        row.addStretch(1)
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
        row.addWidget(self.skip_learning_button, alignment=Qt.AlignmentFlag.AlignTop)
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
        """Show saved city and budget as hints; excluded brands apply to every search.

        The fields stay empty: the core falls back to the saved city and budget only when
        neither a field nor the query names one, as ``--city``/``--budget`` in the CLI.
        """
        city = prefs.get(CITY)
        self.city.setPlaceholderText(f"из запроса, иначе {city}" if city else _CITY_HINT)
        budget = as_budget(prefs.get(BUDGET))
        self.budget.setSpecialValueText(
            f"из запроса, иначе {kzt(budget)}" if budget else _BUDGET_HINT
        )
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
        """A new search from the form: any earlier question is dropped."""
        self._learn_note = ""
        self._launch()

    def rerun(self) -> None:
        """The last search again with its answer and round (after learning a category)."""
        self._launch(*self._last_rounds)

    def _answer(self, reply: str) -> None:
        """The user answered the question: an option goes back as ``answer``, own text
        joins the query; either way the next round runs."""
        asked = self._asked
        if asked is None or self._job is not None:
            return
        if reply in asked.options and asked.kind != "capability":
            self._launch(answer=reply, clarify_round=asked.round + 1)
            return
        self.query.setText(f"{self.query.text().strip()} {reply}")
        self._launch(clarify_round=asked.round + 1)

    def _skip_questions(self) -> None:
        """«Искать без уточнений», Enter in the CLI: the same search, the first option, no more
        questions (for the rerun after learning too)."""
        asked = self._asked
        if asked is None or self._job is not None:
            return
        self._launch(self._last_rounds[0], asked.round + 1, no_clarify=True)

    def _launch(
        self,
        answer: str | None = None,
        clarify_round: int = 0,
        no_clarify: bool = False,
        *,
        learn_first: bool = True,
    ) -> None:
        if self.is_running():
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
        self.outcome_table.hide()
        self.clarify.hide()
        self.recent_box.setVisible(bool(self._recent))
        self._asked = None
        self._set_running(True)
        use_llm = self.use_llm.isChecked()
        summary = self.review_summary.isChecked()
        self._run = SearchRun(query, use_llm, self._states, summary)
        self._last_rounds = (answer, clarify_round, no_clarify)
        rounds: dict[str, Any] = {"answer": answer} if answer is not None else {}
        if clarify_round:
            rounds["clarify_round"] = clarify_round
        if no_clarify:
            rounds["no_clarify"] = True
        coro = self._search(
            query,
            city=self.city.text().strip() or None,
            budget=self.budget.value() or None,
            use_llm=use_llm,
            trusted_only=self.trusted_only.isChecked(),
            with_review_summary=summary,
            sources=selected,
            excluded_brands=self._excluded_brands,
            learn_first=learn_first,
            **rounds,
        )
        self._job = self._runner.submit(self._progress.track(coro))
        self._job.finished.connect(self._on_finished)
        self._job.failed.connect(self._on_failed)
        self._job.cancelled.connect(self._on_cancelled)

    async def _search(
        self, query: str, *, excluded_brands: list[str], **kwargs: Any
    ) -> RecommendResult | NoRecommendation | AskUser | LearnFirst:
        # Runs on the core loop thread: takes plain values only, never touches a widget.
        try:
            if excluded_brands:
                parsed = await self._parse(query, kwargs["use_llm"])
                kwargs["parsed"] = with_excluded(parsed, excluded_brands)
            return await self._recommend(query, **kwargs)
        except NeedsClarification as exc:  # not a ValueError: a question, not "nothing found"
            return AskUser(exc.clarification)
        except NeedsLearning as exc:
            return LearnFirst(exc.category)
        except NothingToSearch as exc:
            return NoRecommendation(str(exc))
        except NoSubstanceMatch as exc:
            return _no_substance(exc)
        except NoNewPick as exc:
            return _no_new_pick(exc)
        except ValidationError:
            raise  # a bug, not "nothing to recommend": the generic message, details in the log
        except ValueError as exc:  # the core's "nothing to recommend" (the CLI prints it too)
            logger.info("No recommendation for %r", query, exc_info=exc)
            return NoRecommendation(no_recommendation_text(exc))
        except httpx.HTTPError as exc:  # the CLI's exit 2: which marketplace failed and why
            logger.warning("Marketplace request failed for %r", query, exc_info=exc)
            return NoRecommendation(marketplace_error_text(exc))

    def is_running(self) -> bool:
        return self._job is not None or self._learning_for is not None

    def cancel(self) -> None:
        if (category := self._learning_for) is not None:
            self._learning_for = None
            self.learning.cancel(category)
            self._set_running(False)
            self.message.setText("Поиск отменён.")
            return
        if self._job is not None:
            self.cancel_button.setEnabled(False)
            self._stage_text = _CANCELLING
            self._show_stage()
            self._job.cancel()

    def _on_progress(self, source: str, state: str, stage: str) -> None:
        if state == "running" and self._job is not None and self._stage_text != _CANCELLING:
            self._stage_text = stage_label(stage)
            self._show_stage()
        if source in self._states:  # a capability may open a source the user did not pick (OLX)
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

    def _wait_for_learning(self, category: str) -> None:
        """Learn the new category first; the search runs again when it ends (#26)."""
        self._learning_for = category
        self.learning.learn_now(category)
        self._set_running(True)
        self._stage_text = _LEARNING.format(category=category)
        self._show_stage()

    def skip_learning(self) -> None:
        """Search now without the books; the learning goes on and the report shows it."""
        if self._learning_for is None:
            return
        self._learning_for = None
        self._launch(*self._last_rounds, learn_first=False)

    def _on_learning_settled(self, category: str) -> None:
        if category != self._learning_for:
            return
        self._learning_for = None
        self._learn_note = self.learning.outcome(category) or ""
        self._launch(*self._last_rounds, learn_first=False)

    def _on_finished(
        self, result: RecommendResult | NoRecommendation | AskUser | LearnFirst
    ) -> None:
        if isinstance(result, LearnFirst):
            self._job = None  # no marketplace ran yet: they keep waiting, not «пропущен»
            self._wait_for_learning(result.category)
            return
        self._settle()
        if isinstance(result, AskUser):
            self._asked = result.clarification
            self.recent_box.hide()  # the question takes its place: the screen does not scroll
            self.clarify.ask(result.clarification)
            return
        if isinstance(result, NoRecommendation):
            self.message.setText(result.message)
            self._show_outcome(result)
            return
        run = replace(self._run, source_states=dict(self._states))
        self._show_counts(result)
        self.report_button.show()
        self.report_ready.emit(result, run)

    def _show_outcome(self, result: NoRecommendation) -> None:
        table, self._outcome_urls = self.outcome_table, result.urls
        table.setColumnCount(len(result.headers))
        table.setHorizontalHeaderLabels(result.headers)
        table.setRowCount(len(result.rows))
        for r, cells in enumerate(result.rows):
            for c, text in enumerate(cells):
                table.setItem(r, c, QTableWidgetItem(text))
        for column, header in enumerate(result.headers):
            if column == 0 or header == COMPARED_HEADER:  # text columns share the width, wrap
                stretch_column(table, column)
        table.setVisible(bool(result.rows))

    def _open_outcome_row(self, row: int, _column: int) -> None:
        if row < len(self._outcome_urls) and (url := self._outcome_urls[row]):
            QDesktopServices.openUrl(QUrl(url))

    def _show_counts(self, result: RecommendResult) -> None:
        """What each marketplace brought, next to its status and as one summary line."""
        counts = found_counts(result)
        for name, label in self.status.items():
            if self._states.get(name) == "done":
                label.setText(f"{STATE_LABELS['done']} · {counts[name]}")
        note, self._learn_note = self._learn_note, ""
        self.message.setText(f"{note} {found_line(counts)}".strip())

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
        self.skip_learning_button.setVisible(self._learning_for is not None)
        self.use_llm.setEnabled(not running)
        self.review_summary.setEnabled(not running and self.use_llm.isChecked())
        self.trusted_only.setEnabled(not running)
        for name, box in self.sources.items():
            box.setEnabled(not running and name not in self._unavailable)
