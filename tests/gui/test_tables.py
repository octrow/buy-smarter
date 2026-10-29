"""Fit tables: a stretched text column shares the spare width but never collapses."""

from PySide6.QtWidgets import QTableWidgetItem

from buysmarter.gui.tables import TEXT_FLOOR_PX, FitTableWidget, stretch_column

_WIDE_CELL = "без гарантии/проверки, самовывоз"


def _table(qtbot, width: int) -> FitTableWidget:
    table = FitTableWidget(1, 4)
    for column, text in enumerate(["Philips Sonicare Power Flosser", _WIDE_CELL, _WIDE_CELL, "x"]):
        table.setItem(0, column, QTableWidgetItem(text))
    stretch_column(table, 0)
    qtbot.addWidget(table)
    table.resize(width, 200)
    table.show()
    qtbot.waitExposed(table)
    return table


def test_the_stretched_column_takes_the_spare_width(qtbot):
    table = _table(qtbot, 1200)
    header = table.horizontalHeader()
    used = lambda: sum(header.sectionSize(c) for c in range(4))  # noqa: E731
    qtbot.waitUntil(lambda: abs(used() - table.viewport().width()) <= 1)
    assert header.sectionSize(0) > TEXT_FLOOR_PX


def test_a_narrow_table_scrolls_sideways_instead_of_collapsing_the_title(qtbot):
    table = _table(qtbot, 400)
    qtbot.waitUntil(lambda: table.horizontalScrollBar().maximum() > 0)
    assert table.horizontalHeader().sectionSize(0) == TEXT_FLOOR_PX
    assert table.horizontalScrollBar().maximum() > 0
