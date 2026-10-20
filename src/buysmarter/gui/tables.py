"""Tables that show every row: the page scrolls, so a table never scrolls on its own.

The height comes from ``sizeHint`` and the column widths from header resize modes, so Qt
recomputes both after a theme, font, sort or filter change instead of keeping stale sizes.
"""

from PySide6.QtCore import QAbstractItemModel, QSize, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QTableWidget


def _fit_hint(view: QTableView, base: QSize) -> QSize:
    model = view.model()
    rows = sum(view.rowHeight(r) for r in range(model.rowCount())) if model else 0
    header = view.horizontalHeader().sizeHint().height()
    return QSize(base.width(), header + rows + 2 * view.frameWidth())


def _configure(view: QTableView) -> None:
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.verticalHeader().setVisible(False)
    view.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    columns = view.horizontalHeader()
    columns.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    columns.setStretchLastSection(True)


def _track_rows(view: QTableView, model: QAbstractItemModel) -> None:
    for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset, model.layoutChanged):
        signal.connect(view.updateGeometry)


class FitTableView(QTableView):
    def __init__(self) -> None:
        super().__init__()
        _configure(self)

    def setModel(self, model: QAbstractItemModel | None) -> None:  # noqa: N802 - Qt's name
        super().setModel(model)
        if model is not None:
            _track_rows(self, model)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return _fit_hint(self, super().sizeHint())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return self.sizeHint()


class FitTableWidget(QTableWidget):
    def __init__(self, rows: int, columns: int) -> None:
        super().__init__(rows, columns)
        _configure(self)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return _fit_hint(self, super().sizeHint())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return self.sizeHint()
