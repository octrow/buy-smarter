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
    # A too-wide table scrolls sideways; its scroll bar must not cover the last row.
    bar = view.horizontalScrollBar()
    scroll = bar.sizeHint().height() if bar.maximum() > 0 else 0
    return QSize(base.width(), header + rows + scroll + 2 * view.frameWidth())


def _configure(view: QTableView) -> None:
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setWordWrap(True)
    rows = view.verticalHeader()
    rows.setVisible(False)
    rows.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    # A wrapped cell grows its row when the window narrows: re-ask the layout for the height,
    # or the last row is clipped (narrow) or a blank strip stays under it (wide).
    rows.sectionResized.connect(lambda *_: view.updateGeometry())
    view.setShowGrid(False)  # rows are ruled by the QSS item border; no vertical lines
    columns = view.horizontalHeader()
    columns.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    columns.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    columns.sectionResized.connect(lambda *_: view.updateGeometry())
    view.horizontalScrollBar().rangeChanged.connect(lambda *_: view.updateGeometry())


def stretch_column(view: QTableView, column: int) -> None:
    """Give the spare width to one text column (a title, a reason), never to a number."""
    view.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)


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
