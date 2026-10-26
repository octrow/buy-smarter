"""Alternatives table: the ranked offers with one column per scoring axis."""

from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)

from buysmarter.cli.report import tier_cell
from buysmarter.gui.labels import BEST_ROLE, ROLE_LABELS, axis_label, clean_title, source_label
from buysmarter.gui.prefs_fields import kzt
from buysmarter.ranking.models import Recommendation, ScoredCandidate
from buysmarter.ranking.selection import distinct_variants, is_barred

SORT_ROLE = Qt.ItemDataRole.UserRole
URL_ROLE = Qt.ItemDataRole.UserRole + 1
_FIXED_COLUMNS = ("Роль", "Маркетплейс", "Товар", "Цена", "Баллы", "Бренд")
SOURCE_COLUMN = _FIXED_COLUMNS.index("Маркетплейс")
PRODUCT_COLUMN = _FIXED_COLUMNS.index("Товар")
SCORE_COLUMN = _FIXED_COLUMNS.index("Баллы")
AXES_START = len(_FIXED_COLUMNS)  # one column per scoring axis from here on

type _Index = QModelIndex | QPersistentModelIndex


class AlternativesModel(QAbstractTableModel):
    """Rows are the distinct ranked offers; ``SORT_ROLE`` holds the raw number for sorting."""

    def __init__(self, recommendation: Recommendation) -> None:
        super().__init__()
        self._rows = distinct_variants(recommendation.ranked, len(recommendation.ranked))
        self._roles = {
            _identity(s): ROLE_LABELS.get(role, role)
            for role, s in reversed(recommendation.alternatives.items())
        }
        self._roles[_identity(recommendation.best)] = BEST_ROLE  # the pick is a row too
        self._axes = list(dict.fromkeys(a.name for s in self._rows for a in s.axes))

    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008 - Qt's signature
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008 - Qt's signature
        return 0 if parent.isValid() else len(_FIXED_COLUMNS) + len(self._axes)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return (*_FIXED_COLUMNS, *map(axis_label, self._axes))[section]

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        scored = self._rows[index.row()]
        if role == URL_ROLE:
            return scored.candidate.url
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(scored, index.column())
        display, raw = self._cell(scored, index.column())
        if role == Qt.ItemDataRole.DisplayRole:
            return display
        if role == SORT_ROLE:
            return raw
        return None

    def _cell(self, scored: ScoredCandidate, column: int) -> tuple[str, Any]:
        cand = scored.candidate
        match column:
            case 0:
                role = self._roles.get(_identity(scored), "")
                return role, role
            case 1:
                return source_label(cand.source), cand.source
            case 2:
                title = clean_title(cand.title)
                title = f"⚠ {title}" if is_barred(scored) else title
                return title, title
            case 3:
                return kzt(cand.price), cand.price
            case 4:
                return f"{scored.total_score:.0f}", scored.total_score
            case 5:
                tier = tier_cell(cand)
                return tier, tier
        name = self._axes[column - len(_FIXED_COLUMNS)]
        axis = next((a for a in scored.axes if a.name == name), None)
        if axis is None:
            return "—", -1.0
        return f"{axis.score:.1f}/{axis.max_score:.0f}", axis.score

    @staticmethod
    def _tooltip(scored: ScoredCandidate, column: int) -> str | None:
        if column == PRODUCT_COLUMN:
            return scored.candidate.title
        if column == SCORE_COLUMN:  # the axes, also when their columns are hidden
            return "\n".join(
                f"{axis_label(a.name)}: {a.score:.1f}/{a.max_score:.0f}" for a in scored.axes
            )
        return None


def _identity(scored: ScoredCandidate) -> tuple[str, str]:
    # The core's candidate identity: ids are only unique within one marketplace.
    return scored.candidate.source, scored.candidate.source_product_id


def alternatives_proxy(model: AlternativesModel) -> QSortFilterProxyModel:
    """Sort on the raw numbers; filter on the Source column (``setFilterFixedString``)."""
    proxy = QSortFilterProxyModel()
    proxy.setSourceModel(model)
    proxy.setSortRole(SORT_ROLE)
    proxy.setFilterKeyColumn(SOURCE_COLUMN)
    return proxy
