"""Report screen: the explainable result of one search, in the CLI report's terms."""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

from buysmarter.cli.report import format_rejected_reasons
from buysmarter.gui.prefs_fields import kzt
from buysmarter.gui.screens.alternatives import (
    SCORE_COLUMN,
    URL_ROLE,
    AlternativesModel,
    alternatives_proxy,
)
from buysmarter.gui.screens.search import SearchRun
from buysmarter.gui.tables import FitTableView, FitTableWidget
from buysmarter.gui.theme import SPACE, set_state
from buysmarter.ranking.models import QueryConstraints, ScoredCandidate
from buysmarter.ranking.selection import is_barred
from buysmarter.services.matching import UsedListing
from buysmarter.services.recommend import RecommendResult

ALL_MARKETPLACES = "Все маркетплейсы"
_BAR_COLUMN = 2
_BAR_WIDTH = 120
_BAR_STEPS = 100


def open_offer(url: str | None) -> None:
    """Open an offer's marketplace page in the system web browser."""
    if url:
        QDesktopServices.openUrl(QUrl(url))


def constraints_line(c: QueryConstraints, city_defaulted: bool) -> str:
    city = f"{c.city or '-'}{' (по умолчанию)' if city_defaulted else ''}"
    budget = kzt(c.budget_max) if c.budget_max else "без лимита"
    parts = [f"Category: {c.category}", f"City: {city}", f"Budget: {budget}"]
    if c.required_features:
        parts.append(f"Required: {', '.join(c.required_features)}")
    if c.excluded_brands:
        parts.append(f"Excluded: {', '.join(c.excluded_brands)}")
    return "   ".join(parts)


def best_notes(best: ScoredCandidate) -> list[tuple[str, str]]:
    """``(state, text)`` notes under the best pick, worded as the CLI's "Best pick" panel."""
    notes = []
    if is_barred(best):
        notes.append(("warning", "все кандидаты под подозрением — проверьте вручную"))
    if best.review_penalty > 0:
        notes.append(("warning", f"Penalty: отзывы (−{best.review_penalty:.0f})"))
    if best.candidate.best_seller:
        notes.append(("", f"Продавец: {best.candidate.best_seller}"))
    return notes


def _label(text: str, *, name: str = "", role: str = "", state: str = "") -> QLabel:
    label = QLabel(text, wordWrap=True)
    if name:
        label.setObjectName(name)
    for prop, value in (("role", role), ("state", state)):
        if value:
            label.setProperty(prop, value)
    return label


def _metric(value: str, caption: str, name: str) -> QWidget:
    box = QWidget()
    column = QVBoxLayout(box)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(SPACE["xs"] // 2)
    column.addWidget(_label(value, name=name))
    column.addWidget(_label(caption, role="caption"))
    return box


def _axis_bar(score: float, max_score: float) -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, _BAR_STEPS)
    bar.setValue(round(_BAR_STEPS * score / max_score) if max_score else 0)
    bar.setTextVisible(False)
    return bar


def rejected_line(result: RecommendResult) -> str:
    rec = result.recommendation
    if not rec.rejected_count:
        return "Hard filters rejected nothing."
    line = f"{rec.rejected_count} candidate(s) rejected by hard filters."
    if rec.rejected_reasons:
        line += f"\nОтсеяно: {format_rejected_reasons(rec.rejected_reasons)}"
    return line


def could_not_verify(result: RecommendResult, run: SearchRun) -> list[str]:
    """Each marketplace or LLM step that did not contribute, and why."""
    lines = []
    for source, state in run.source_states.items():
        if source in result.unavailable_sources:
            lines.append(
                f"{source}: пропущен — нет браузера; что сделать, смотрите в «Состояние и ключи»"
            )
        elif state == "skipped":
            lines.append(f"{source}: пропущен — не выбран или поиск до него не дошёл")
        elif state == "failed":
            lines.append(f"{source}: ошибка — его предложений нет в отчёте (подробности в логе)")
    if result.skipped_llm_steps:
        lines.append(
            "AI не настроен: запрос разобран по ключевым словам, без сводки отзывов и разбора "
            "состава. Ключ можно добавить в «Состояние и ключи»"
        )
    elif not run.use_llm:
        lines.append("AI выключен: запрос разобран по ключевым словам, сводки отзывов нет")
    elif result.review_summary is None:
        lines.append("Сводка отзывов недоступна")
    return lines


def text_table(headers: list[str], rows: list[list[str]]) -> QTableWidget:
    table = FitTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(text))
    return table


def _used_row(listing: UsedListing) -> list[str]:
    title = f"{listing.title} → {listing.canonical}" if listing.canonical else listing.title
    saving = "—" if listing.standalone else f"-{kzt(listing.saving)}"
    return [title, kzt(listing.price), saving, listing.city or "-", listing.con]


def _group(title: str, *widgets: QWidget) -> QGroupBox:
    box = QGroupBox(title)
    layout = QVBoxLayout(box)
    for widget in widgets:
        layout.addWidget(widget)
    return box


class ReportScreen(QScrollArea):
    """Shows one ``RecommendResult``; ``show_result`` replaces whatever was shown before."""

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        empty = _label(
            "Здесь появится отчёт после поиска.\nНачните с экрана «Поиск».", role="muted"
        )
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWidget(empty)

    def show_result(self, result: RecommendResult, run: SearchRun) -> None:
        # Optional sections: drop handles into the page that setWidget below deletes.
        self.open_best: QPushButton | None = None
        self.used: QTableWidget | None = None
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE["xl"], SPACE["sm"], SPACE["xl"], SPACE["xl"])
        layout.setSpacing(SPACE["xs"])
        constraints = constraints_line(result.constraints, result.city_defaulted)
        layout.addWidget(_label(constraints, role="muted"))
        layout.addWidget(self._best_section(result.recommendation.best))
        layout.addWidget(self._alternatives_section(result))
        layout.addWidget(_group("Rejected", _label(rejected_line(result), role="muted")))
        if result.used_listings:
            layout.addWidget(self._used_section(result.used_listings))
        if lines := could_not_verify(result, run):
            notes = _label("\n".join(lines), state="warning")
            layout.addWidget(_group("Не удалось проверить", notes))
        layout.addStretch(1)
        self.setWidget(page)

    def _best_section(self, best: ScoredCandidate) -> QGroupBox:
        cand = best.candidate
        title = f"⚠ {cand.title}" if is_barred(best) else cand.title
        metrics = QHBoxLayout()
        metrics.setSpacing(SPACE["xxl"])
        metrics.addWidget(_metric(f"{best.total_score:.0f}/100", "Score", "bestScore"))
        metrics.addWidget(_metric(f"{best.confidence:.0%}", "Confidence", "bestConfidence"))
        metrics.addWidget(_metric(kzt(cand.price), "Price", "bestPrice"))
        metrics.addStretch(1)
        if url := cand.url:
            self.open_best = QPushButton("Открыть на маркетплейсе")
            set_state(self.open_best, "primary", "true")
            self.open_best.clicked.connect(lambda: open_offer(url))
            metrics.addWidget(self.open_best, 0, Qt.AlignmentFlag.AlignBottom)
        figures = QWidget()
        figures.setLayout(metrics)

        axes = text_table(
            ["Axis", "Score", "", "Why"],
            [[a.name, f"{a.score:.1f}/{a.max_score:.0f}", "", a.explanation] for a in best.axes],
        )
        for row, axis in enumerate(best.axes):
            axes.setCellWidget(row, _BAR_COLUMN, _axis_bar(axis.score, axis.max_score))
        axes.horizontalHeader().setSectionResizeMode(_BAR_COLUMN, QHeaderView.ResizeMode.Fixed)
        axes.setColumnWidth(_BAR_COLUMN, _BAR_WIDTH)
        self.best_axes = axes

        widgets: list[QWidget] = [
            _label(title, name="bestTitle"),
            _label(cand.source, role="caption"),
            figures,
        ]
        widgets += [_label(text, state=state) for state, text in best_notes(best)]
        widgets.append(axes)
        if best.pros:
            widgets.append(_label("+ " + "  + ".join(best.pros), state="ok"))
        if best.cons:
            widgets.append(_label("- " + "  - ".join(best.cons), state="error"))
        return _group("Best pick", *widgets)

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
        self.marketplace = QComboBox()
        self.marketplace.addItems([ALL_MARKETPLACES, *sorted(self._sources(result))])
        self.marketplace.currentTextChanged.connect(self._filter_marketplace)
        row = QHBoxLayout()
        row.addWidget(QLabel("Маркетплейс"))
        row.addWidget(self.marketplace)
        row.addStretch(1)
        filters = QWidget()
        filters.setLayout(row)
        return _group("Alternatives", filters, view)

    def _filter_marketplace(self, text: str) -> None:
        self.alternatives_proxy.setFilterFixedString("" if text == ALL_MARKETPLACES else text)

    @staticmethod
    def _sources(result: RecommendResult) -> set[str]:
        return {s.candidate.source for s in result.recommendation.ranked}

    def _used_section(self, used: list[UsedListing]) -> QGroupBox:
        table = text_table(
            ["Listing", "Price", "Saving vs new", "City", "Con"], [_used_row(u) for u in used]
        )
        table.cellDoubleClicked.connect(lambda row, _col: open_offer(used[row].url))
        self.used = table
        return _group("Б/у на OLX", table)
