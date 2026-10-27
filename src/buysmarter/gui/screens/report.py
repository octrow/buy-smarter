"""Report screen: the explainable result of one search, in the CLI report's terms."""

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from buysmarter.cli.report import (
    COMPARED_HEADER,
    SUMMARY_TITLE,
    USED_HEADERS,
    assessed_note,
    brands_missing_note,
    budget_text,
    hidden_note,
    ingredients_unchecked_note,
    rejected_line,
    request_notes,
    summary_lines,
    summary_note,
    used_cells,
    used_first,
)
from buysmarter.cli.style import votes_work
from buysmarter.gui.bridge import CoreRunner
from buysmarter.gui.capability_toggles import TITLE as TOGGLES_TITLE
from buysmarter.gui.capability_toggles import CapabilityToggles
from buysmarter.gui.labels import (
    USED_SECTION,
    axis_label,
    checked_brands,
    clean_title,
    confidence_hint,
    explain,
    pro_con,
    source_label,
)
from buysmarter.gui.learning import CategoryLearning, Learn
from buysmarter.gui.prefs_fields import kzt
from buysmarter.gui.screens.alternatives import (
    AXES_START,
    CANDIDATE_ROLE,
    PRODUCT_COLUMN,
    SCORE_COLUMN,
    URL_ROLE,
    AlternativesModel,
    alternatives_proxy,
)
from buysmarter.gui.screens.search import SearchRun
from buysmarter.gui.style_votes import StyleVotes
from buysmarter.gui.tables import FitTableView, FitTableWidget, stretch_column
from buysmarter.gui.theme import CONTENT_MAX_WIDTH, SPACE, card_body, set_state
from buysmarter.knowledge.generate import learn_category
from buysmarter.ranking.models import Candidate, QueryConstraints, ScoredCandidate
from buysmarter.ranking.selection import is_barred
from buysmarter.services.auto_learn import limit_note, over_limit
from buysmarter.services.recommend import RecommendResult

ALL_MARKETPLACES = "Все маркетплейсы"
_BAR_COLUMN = 2
_WHY_COLUMN = 3
_BAR_WIDTH = 160
_BAR_STEPS = 100
_LOW_SHARE = 0.4  # an axis bar under this share of its max reads as a weak spot
_HIGH_SHARE = 0.8
_TICK_MS = 1000
_RERUN_TEXT = "Повторить поиск"
SELECTED_HINT = "— для выбранной строки"
_OPEN_HINT = "Двойной клик по строке — открыть предложение на маркетплейсе."
_USED_HINT = (
    "Если модель объявления не распознана, оно сравнивается с ближайшим новым товаром "
    "по цене — проверьте, что это то же самое."
)


def open_offer(url: str | None) -> None:
    """Open an offer's marketplace page in the system web browser."""
    if url:
        QDesktopServices.openUrl(QUrl(url))


def constraints_line(c: QueryConstraints, city_defaulted: bool) -> str:
    city = f"{c.city or '—'}{' (по умолчанию)' if city_defaulted else ''}"
    parts = [f"Категория: {c.search_phrase}", f"Город: {city}", f"Бюджет: {budget_text(c)}"]
    if c.required_features:
        parts.append(f"Нужно: {', '.join(c.required_features)}")
    if c.excluded_brands:
        parts.append(f"Без брендов: {', '.join(c.excluded_brands)}")
    return "  ·  ".join(parts)


def best_notes(best: ScoredCandidate) -> list[tuple[str, str]]:
    """``(state, text)`` notes under the best pick, worded as the CLI's "Best pick" panel."""
    notes = []
    if is_barred(best):
        notes.append(("warning", "все кандидаты под подозрением — проверьте вручную"))
    if best.review_penalty > 0:
        notes.append(("warning", f"Штраф по отзывам: −{best.review_penalty:.0f}"))
    if best.candidate.best_seller:
        notes.append(("", f"Продавец: {best.candidate.best_seller}"))
    return notes


def _category(result: RecommendResult) -> str:
    return result.constraints.product_type or result.constraints.search_phrase


def _label(text: str, *, name: str = "", role: str = "", state: str = "") -> QLabel:
    label = QLabel(text, wordWrap=True)
    if name:
        label.setObjectName(name)
    for prop, value in (("role", role), ("state", state)):
        if value:
            label.setProperty(prop, value)
    return label


def _metric(value: str, caption: str, name: str, hint: str = "") -> QWidget:
    box = QWidget()
    box.setToolTip(hint)
    column = QVBoxLayout(box)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(SPACE["xs"] // 2)
    column.addWidget(_label(value, name=name))
    column.addWidget(_label(caption, role="caption"))
    return box


def bar_level(score: float, max_score: float) -> str:
    """``low`` / ``high`` / ``""``: the bar's color, so a weak axis stands out at a glance."""
    share = score / max_score if max_score else 0.0
    return "low" if share < _LOW_SHARE else "high" if share >= _HIGH_SHARE else ""


def _axis_bar(score: float, max_score: float) -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, _BAR_STEPS)
    bar.setValue(round(_BAR_STEPS * score / max_score) if max_score else 0)
    bar.setTextVisible(False)
    bar.setProperty("level", bar_level(score, max_score))
    return bar


def could_not_verify(result: RecommendResult, run: SearchRun) -> list[str]:
    """Each marketplace or LLM step that did not contribute, and why."""
    lines = []
    for source, state in run.source_states.items():
        name = source_label(source)
        if source in result.unavailable_sources:
            lines.append(
                f"{name}: пропущен — нет браузера; что сделать, смотрите в «Состояние и ключи»"
            )
        elif state == "skipped":
            lines.append(f"{name}: пропущен — не выбран или поиск до него не дошёл")
        elif state == "failed":
            lines.append(f"{name}: ошибка — его предложений нет в отчёте (подробности в логе)")
    if result.skipped_llm_steps:
        lines.append(
            "AI не настроен: запрос разобран по ключевым словам, без сводки отзывов и разбора "
            "состава. Ключ можно добавить в «Состояние и ключи»"
        )
    elif note := summary_note(result, use_llm=run.use_llm, asked=run.review_summary):
        lines.append(note.removesuffix("."))
    if note := ingredients_unchecked_note(result):
        lines.append(note.removesuffix("."))
    return lines


def text_table(
    headers: list[str], rows: list[list[str]], stretch: int | None = None
) -> QTableWidget:
    table = FitTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(text))
    if stretch is not None:
        stretch_column(table, stretch)
    return table


# The label state of each summary line kind (``cli.report.summary_lines``).
_SUMMARY_STATE = {"flag": "warning", "pro": "ok", "con": "error", "complaint": "", "verdict": ""}


def _axes_table(best: ScoredCandidate) -> QTableWidget:
    """One row per scoring axis: name, score/max, a colored bar, and the reason."""
    axes = text_table(
        ["Ось", "Баллы", "", "Почему"],
        [
            [axis_label(a.name), f"{a.score:.1f}/{a.max_score:.0f}", "", explain(a.explanation)]
            for a in best.axes
        ],
        stretch=_WHY_COLUMN,
    )
    for row, axis in enumerate(best.axes):
        axes.setCellWidget(row, _BAR_COLUMN, _axis_bar(axis.score, axis.max_score))
    axes.horizontalHeader().setSectionResizeMode(_BAR_COLUMN, QHeaderView.ResizeMode.Fixed)
    axes.setColumnWidth(_BAR_COLUMN, _BAR_WIDTH)
    return axes


def _group(title: str, *widgets: QWidget) -> QGroupBox:
    box = QGroupBox(title)
    layout = card_body(QVBoxLayout(box))
    layout.setSpacing(SPACE["sm"])
    for widget in widgets:
        layout.addWidget(widget)
    return box


def _row(*widgets: QWidget, spacing: int = SPACE["md"]) -> QWidget:
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    for widget in widgets:
        row.addWidget(widget)
    row.addStretch(1)
    return holder


class ReportScreen(QScrollArea):
    """Shows one ``RecommendResult``; ``show_result`` replaces whatever was shown before.

    Emits ``rerun_requested`` when the user asks to search again (after learning a category).
    """

    rerun_requested = Signal()

    def __init__(
        self,
        runner: CoreRunner | None = None,
        learn: Learn = learn_category,
        learning: CategoryLearning | None = None,
    ) -> None:
        super().__init__()
        self._runner = runner
        # Shared with the main window, so a learn outlives the report that started it.
        self.learning = learning or CategoryLearning(runner, learn)
        self._category: str | None = None  # the on-screen report's, when it lacks knowledge
        self._learn_blocked = False
        self._rerun_offered = False
        self._ticker = QTimer(self, interval=_TICK_MS)
        self._ticker.timeout.connect(self._render_learning)
        self.learning.started.connect(self._on_learning_changed)
        self.learning.settled.connect(self._on_learning_changed)
        self.setWidgetResizable(True)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        empty = _label(
            "Здесь появится отчёт после поиска.\nНачните с экрана «Поиск».", role="muted"
        )
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWidget(empty)

    def show_result(self, result: RecommendResult, run: SearchRun) -> None:
        # Optional sections: drop handles into the page that setWidget below deletes.
        self.open_best: QPushButton | None = None
        self.used: QTableWidget | None = None
        self.learn_button: QPushButton | None = None
        self.rejected: QGroupBox | None = None
        self.toggles: CapabilityToggles | None = None
        self.configure_link: QPushButton | None = None
        self.like_best: QPushButton | None = None
        self.dislike_selected: QPushButton | None = None
        self.style_message: QLabel | None = None
        self._votes = self._runner is not None and votes_work(result)
        self._category = None
        self._ticker.stop()
        self._rerun_offered = False
        self._learn_blocked = False
        page = QWidget()
        page.setObjectName("page")
        page.setMaximumWidth(CONTENT_MAX_WIDTH)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE["xxl"], SPACE["xxl"], SPACE["xxl"], SPACE["xl"])
        layout.setSpacing(SPACE["lg"])
        if result.restricted_note:
            layout.addWidget(_label(f"⚠ {result.restricted_note}", state="warning"))
        heading = QVBoxLayout()
        heading.setSpacing(SPACE["xs"])
        heading.addWidget(_label(run.query, name="hero"))
        constraints = constraints_line(result.constraints, result.city_defaulted)
        heading.addWidget(_label(constraints, role="muted"))
        for note in request_notes(result):
            heading.addWidget(_label(note, state="warning"))
        # The switches close the page; this link at the top leads there.
        self.configure_link = QPushButton("Настроить")
        set_state(self.configure_link, "flat", "true")
        self.configure_link.setAccessibleName(f"Настроить: {TOGGLES_TITLE}")
        self.configure_link.clicked.connect(self._show_switches)
        assessed = QHBoxLayout()
        assessed.setSpacing(SPACE["md"])
        if how := assessed_note(result):
            assessed.addWidget(_label(how, role="muted"), 1)
        assessed.addWidget(self.configure_link, 0, Qt.AlignmentFlag.AlignTop)
        if not how:
            assessed.addStretch(1)
        heading.addLayout(assessed)
        layout.addLayout(heading)
        if result.knowledge_missing:
            layout.addWidget(self._knowledge_banner(result))
        self._add_used(layout, result, first=True)
        layout.addWidget(self._best_section(result.recommendation.best))
        if result.review_summary is not None and (said := summary_lines(result.review_summary)):
            widgets = [_label(text, state=_SUMMARY_STATE[kind]) for kind, text in said]
            layout.addWidget(_group(SUMMARY_TITLE, *widgets))
        layout.addWidget(self._alternatives_section(result))
        if result.recommendation.rejected_count:
            self.rejected = _group("Отсеяно", _label(rejected_line(result), role="muted"))
            layout.addWidget(self.rejected)
        self._add_used(layout, result, first=False)
        if hidden := self._hidden_section(result):
            layout.addWidget(hidden)
        layout.addWidget(self._toggles_section(result))
        if lines := could_not_verify(result, run):
            notes = _label("\n".join(lines), state="warning")
            layout.addWidget(_group("Не удалось проверить", notes))
        layout.addStretch(1)
        self.setWidget(page)
        self._after_shown(result)

    def _add_used(self, layout: QVBoxLayout, result: RecommendResult, *, first: bool) -> None:
        """The OLX section, leading the page of a used query and closing the others."""
        if result.used_listings and used_first(result) == first:
            layout.addWidget(self._used_section(result))

    def _hidden_section(self, result: RecommendResult) -> QGroupBox | None:
        """The suspected fakes "только оригинал" hid, each by title."""
        note = hidden_note(result)
        if note is None:
            return None
        titles = [_label(clean_title(s.candidate.title), role="muted") for s in result.hidden]
        self.hidden = _group("Скрытые подозрительные", _label(note, role="muted"), *titles)
        return self.hidden

    def _show_switches(self) -> None:
        if self.toggles is not None:
            self.ensureWidgetVisible(self.toggles)

    def _toggles_section(self, result: RecommendResult) -> CapabilityToggles:
        toggles = CapabilityToggles(self._runner, _category(result), result.capabilities)
        toggles.rerun_requested.connect(self.rerun_requested)
        self.toggles = toggles
        return toggles

    def _after_shown(self, result: RecommendResult) -> None:
        """Show this category's learning state; the search starts a new category's learning."""
        if not result.knowledge_missing:
            return
        category = self._category = _category(result)
        self.learning.forget(category)
        self._render_learning()

    def _knowledge_banner(self, result: RecommendResult) -> QFrame:
        # Learned under the profile key, so the next search finds it (phase 2, decision 13).
        category = _category(result)
        banner = QFrame()
        banner.setObjectName("banner")
        column = QVBoxLayout(banner)
        column.setContentsMargins(SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["md"])
        column.addWidget(_label(brands_missing_note(result) or ""))  # the console's line too
        self.learn_button = QPushButton("Изучить категорию")
        set_state(self.learn_button, "primary", "true")
        self.learn_status = _label("")
        self.learn_button.setAccessibleName("Изучить категорию")
        self.learn_status.setAccessibleName("Статус изучения категории")
        if result.skipped_llm_steps or self._runner is None:
            self._learn_blocked = True
            self.learn_button.setEnabled(False)
            self.learn_status.setText("Нужен AI: настройте его на экране «Состояние и ключи».")
        elif over_limit():
            self.learn_status.setText(f"{limit_note()} Изучите кнопкой «Изучить категорию».")
        self.learn_button.clicked.connect(lambda: self._on_learn_clicked(category))
        column.addWidget(_row(self.learn_button, self.learn_status))
        return banner

    def _on_learn_clicked(self, category: str) -> None:
        if self._rerun_offered:
            self.rerun_requested.emit()
        else:
            self.start_learning(category)

    def start_learning(self, category: str) -> None:
        if self._runner is None or self.learn_button is None:
            return
        self.learning.start(category)

    def _on_learning_changed(self, category: str) -> None:
        if category == self._category:  # another category's learning is not this report's
            self._render_learning()

    def _render_learning(self) -> None:
        """Draw the on-screen category's state in the banner: running, learned or failed."""
        category, button = self._category, self.learn_button
        if category is None or button is None or self._learn_blocked:
            return
        if self.learning.running(category):
            self._ticker.start()
            secs = self.learning.elapsed_s(category)
            self._set_learn_status(f"Изучаю категорию… {secs} с", "")
            button.setEnabled(False)
            return
        self._ticker.stop()
        outcome = self.learning.outcome(category)
        if outcome is None:
            return
        self._rerun_offered = outcome == ""
        button.setEnabled(True)
        if outcome == "":
            self._set_learn_status(f"Готово: бренды для «{category}» сохранены.", "ok")
            button.setText(_RERUN_TEXT)
        else:
            self._set_learn_status(outcome, "error")

    def _set_learn_status(self, text: str, state: str) -> None:
        self.learn_status.setText(text)
        set_state(self.learn_status, "state", state)

    def _votes_for(self, picked: Callable[[], Candidate | None]) -> StyleVotes | None:
        """👍/👎 for a card, when style works for this report (``style_votes``)."""
        if not self._votes or self._runner is None:
            return None
        return StyleVotes(self._runner, picked)

    def _best_section(self, best: ScoredCandidate) -> QGroupBox:
        cand = best.candidate
        title = clean_title(cand.title)
        title = f"⚠ {title}" if is_barred(best) else title
        metrics = QHBoxLayout()
        metrics.setSpacing(SPACE["xxl"])
        metrics.addWidget(_metric(f"{best.total_score:.0f}/100", "Баллы", "bestScore"))
        hint = confidence_hint(cand)
        metrics.addWidget(_metric(f"{best.confidence:.0%}", "Уверенность", "bestConfidence", hint))
        metrics.addWidget(_metric(kzt(cand.price), "Цена", "bestPrice"))
        if url := cand.url:
            self.open_best = QPushButton("Открыть на маркетплейсе")
            set_state(self.open_best, "primary", "true")
            self.open_best.clicked.connect(lambda: open_offer(url))
            metrics.addWidget(self.open_best, 0, Qt.AlignmentFlag.AlignBottom)
        metrics.addStretch(1)
        figures = QWidget()
        figures.setLayout(metrics)

        self.best_axes = _axes_table(best)

        widgets: list[QWidget] = [
            _label(title, name="bestTitle"),
            _label(source_label(cand.source), role="caption"),
            figures,
        ]
        widgets += [_label(text, state=state) for state, text in best_notes(best)]
        if votes := self._votes_for(lambda: cand):
            self.like_best, self.style_message = votes.like, votes.message
            widgets.append(votes)
        widgets.append(self.best_axes)
        if best.pros:
            widgets.append(_label("+ " + "   + ".join(map(pro_con, best.pros)), state="ok"))
        if best.cons:
            widgets.append(_label("− " + "   − ".join(map(pro_con, best.cons)), state="error"))
        return _group("Лучший выбор", *widgets)

    def _alternatives_section(self, result: RecommendResult) -> QGroupBox:
        self.alternatives = AlternativesModel(result.recommendation)
        self.alternatives_proxy = alternatives_proxy(self.alternatives)
        view = self.alternatives_view = FitTableView()
        view.setModel(self.alternatives_proxy)
        view.setSortingEnabled(True)
        view.sortByColumn(SCORE_COLUMN, Qt.SortOrder.DescendingOrder)  # the ranking order
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.verticalHeader().setVisible(False)
        view.doubleClicked.connect(lambda index: open_offer(index.data(URL_ROLE)))
        stretch_column(view, PRODUCT_COLUMN)
        self.marketplace = QComboBox()
        sources = sorted(source_label(s) for s in self._sources(result))
        self.marketplace.addItems([ALL_MARKETPLACES, *sources])
        self.marketplace.currentTextChanged.connect(self._filter_marketplace)
        # The per-axis columns make the table too wide for a laptop window: off by default,
        # and the score cell's tooltip lists them anyway.
        self.show_axes = QCheckBox("Баллы по осям")
        self.show_axes.toggled.connect(self._toggle_axes)
        self._toggle_axes(False)
        filters = _row(QLabel("Маркетплейс"), self.marketplace, self.show_axes, spacing=SPACE["lg"])
        widgets: list[QWidget] = [filters, view, _label(_OPEN_HINT, role="caption")]
        if votes := self._votes_for(self._selected_candidate):
            self.dislike_selected = votes.dislike
            votes.set_enabled(False)
            votes.message.setText(SELECTED_HINT)
            view.selectionModel().selectionChanged.connect(
                lambda: votes.set_enabled(self._selected_candidate() is not None)
            )
            widgets.append(votes)
        return _group("Другие варианты", *widgets)

    def _selected_candidate(self) -> Candidate | None:
        rows = self.alternatives_view.selectionModel().selectedRows()
        return rows[0].data(CANDIDATE_ROLE) if rows else None

    def _toggle_axes(self, shown: bool) -> None:
        for column in range(AXES_START, self.alternatives.columnCount()):
            self.alternatives_view.setColumnHidden(column, not shown)

    def _filter_marketplace(self, text: str) -> None:
        self.alternatives_proxy.setFilterFixedString("" if text == ALL_MARKETPLACES else text)

    @staticmethod
    def _sources(result: RecommendResult) -> set[str]:
        return {s.candidate.source for s in result.recommendation.ranked}

    def _used_section(self, result: RecommendResult) -> QGroupBox:
        used = result.used_listings
        brands = checked_brands(s.candidate for s in result.recommendation.ranked)
        table = text_table(USED_HEADERS, [used_cells(u, brands) for u in used], stretch=0)
        # A long model name must not squeeze the ads: both text columns share the width and wrap.
        stretch_column(table, USED_HEADERS.index(COMPARED_HEADER))
        table.cellDoubleClicked.connect(lambda row, _col: open_offer(used[row].url))
        self.used = table
        return _group(USED_SECTION, table, _label(_USED_HINT, role="caption"))
