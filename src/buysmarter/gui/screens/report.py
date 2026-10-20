"""Report screen: the explainable result of one search, in the CLI report's terms."""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from buysmarter.cli.report import format_rejected_reasons
from buysmarter.gui.screens.alternatives import (
    SCORE_COLUMN,
    URL_ROLE,
    AlternativesModel,
    alternatives_proxy,
)
from buysmarter.gui.screens.search import SearchRun
from buysmarter.ranking.models import QueryConstraints, ScoredCandidate
from buysmarter.ranking.selection import is_barred
from buysmarter.services.matching import UsedListing
from buysmarter.services.recommend import RecommendResult

ALL_MARKETPLACES = "Все маркетплейсы"


def open_offer(url: str | None) -> None:
    """Open an offer's marketplace page in the system web browser."""
    if url:
        QDesktopServices.openUrl(QUrl(url))


def constraints_line(c: QueryConstraints, city_defaulted: bool) -> str:
    city = f"{c.city or '-'}{' (по умолчанию)' if city_defaulted else ''}"
    budget = f"{c.budget_max:,} ₸" if c.budget_max else "без лимита"
    parts = [f"Category: {c.category}", f"City: {city}", f"Budget: {budget}"]
    if c.required_features:
        parts.append(f"Required: {', '.join(c.required_features)}")
    if c.excluded_brands:
        parts.append(f"Excluded: {', '.join(c.excluded_brands)}")
    return "   ".join(parts)


def best_header(best: ScoredCandidate) -> list[str]:
    """The best-pick card lines, as the CLI's "Best pick" panel prints them."""
    cand = best.candidate
    barred = is_barred(best)
    lines = [
        f"🥇 {'⚠ ' if barred else ''}{cand.title}  [{cand.source}]",
        f"Score: {best.total_score:.0f}/100   Confidence: {best.confidence:.0%}",
        f"Price: {cand.price:,} ₸",
    ]
    if barred:
        lines.append("все кандидаты под подозрением — проверьте вручную")
    if best.review_penalty > 0:
        lines.append(f"Penalty: отзывы (−{best.review_penalty:.0f})")
    if cand.best_seller:
        lines.append(f"Продавец: {cand.best_seller}")
    return lines


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
        if state == "skipped":
            lines.append(f"{source}: пропущен — не выбран или поиск до него не дошёл")
        elif state == "failed":
            lines.append(f"{source}: ошибка — его предложений нет в отчёте (подробности в логе)")
    if not run.use_llm:
        lines.append("AI выключен: запрос разобран по ключевым словам, сводки отзывов нет")
    elif result.review_summary is None:
        lines.append("Сводка отзывов недоступна")
    return lines


def fit_to_rows(view: QTableView) -> None:
    """Show every row: the page scrolls, so a table never scrolls on its own."""
    view.resizeColumnsToContents()
    view.resizeRowsToContents()
    view.horizontalHeader().setStretchLastSection(True)
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    rows = sum(view.rowHeight(r) for r in range(view.model().rowCount()))
    view.setFixedHeight(view.horizontalHeader().height() + rows + 2 * view.frameWidth())


def _table(headers: list[str], rows: list[list[str]]) -> QTableWidget:
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(text))
    fit_to_rows(table)
    return table


def _used_row(listing: UsedListing) -> list[str]:
    title = f"{listing.title} → {listing.canonical}" if listing.canonical else listing.title
    saving = "—" if listing.standalone else f"-{listing.saving:,} ₸"
    return [title, f"{listing.price:,} ₸", saving, listing.city or "-", listing.con]


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
        self.setWidget(QLabel("Здесь появится отчёт после поиска."))

    def show_result(self, result: RecommendResult, run: SearchRun) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(constraints_line(result.constraints, result.city_defaulted)))
        layout.addWidget(self._best_section(result.recommendation.best))
        layout.addWidget(self._alternatives_section(result))
        layout.addWidget(_group("Rejected", QLabel(rejected_line(result))))
        if result.used_listings:
            layout.addWidget(self._used_section(result.used_listings))
        if lines := could_not_verify(result, run):
            layout.addWidget(_group("Не удалось проверить", QLabel("\n".join(lines))))
        layout.addStretch(1)
        self.setWidget(page)

    def _best_section(self, best: ScoredCandidate) -> QGroupBox:
        header = QLabel("\n".join(best_header(best)))
        header.setObjectName("bestHeader")
        axes = _table(
            ["Axis", "Score", "Why"],
            [[a.name, f"{a.score:.1f}/{a.max_score:.0f}", a.explanation] for a in best.axes],
        )
        self.best_axes = axes
        widgets: list[QWidget] = [header, axes]
        if best.pros:
            widgets.append(QLabel("+ " + "  + ".join(best.pros)))
        if best.cons:
            widgets.append(QLabel("- " + "  - ".join(best.cons)))
        if url := best.candidate.url:
            self.open_best = QPushButton("Открыть на маркетплейсе")
            self.open_best.clicked.connect(lambda: open_offer(url))
            widgets.append(self.open_best)
        return _group("Best pick", *widgets)

    def _alternatives_section(self, result: RecommendResult) -> QGroupBox:
        self.alternatives = AlternativesModel(result.recommendation)
        self.alternatives_proxy = alternatives_proxy(self.alternatives)
        view = self.alternatives_view = QTableView()
        view.setModel(self.alternatives_proxy)
        view.setSortingEnabled(True)
        view.sortByColumn(SCORE_COLUMN, Qt.SortOrder.DescendingOrder)  # the ranking order
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.verticalHeader().setVisible(False)
        view.doubleClicked.connect(lambda index: open_offer(index.data(URL_ROLE)))
        fit_to_rows(view)
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
        table = _table(
            ["Listing", "Price", "Saving vs new", "City", "Con"], [_used_row(u) for u in used]
        )
        table.cellDoubleClicked.connect(lambda row, _col: open_offer(used[row].url))
        self.used = table
        return _group("Б/у на OLX", table)
