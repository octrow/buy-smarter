"""Past searches: saved markdown reports listed newest first and rendered."""

from pathlib import Path

from buysmarter.cli.report import report_label
from buysmarter.gui.screens.saved import SavedScreen


def test_report_label_reads_the_date_and_query_from_the_file_name():
    path = Path("20261021-203820-смартфон-до-200000-алматы.md")
    assert report_label(path) == "21.10.2026 20:38 — смартфон до 200000 алматы"
    assert report_label(Path("notes.md")) == "notes"


def test_the_newest_report_opens_and_another_can_be_picked(qtbot, tmp_path):
    old = tmp_path / "20261019-184310-гитара.md"
    new = tmp_path / "20261021-203820-смартфон.md"
    old.write_text("# Гитара\n", encoding="utf-8")
    new.write_text("# Смартфон\n", encoding="utf-8")
    screen = SavedScreen(list_reports=lambda: [new, old])
    qtbot.addWidget(screen)

    screen.reload()

    assert screen.view.toPlainText().strip() == "Смартфон"
    screen.reports.setCurrentRow(1)
    assert screen.view.toPlainText().strip() == "Гитара"
    screen.reload()  # the open report stays selected
    assert screen.reports.currentRow() == 1


def test_a_report_shows_its_whole_query_in_the_list_but_not_in_the_text(qtbot, tmp_path):
    saved = tmp_path / "20261021-203820-смартфон-до-200-000-алматы.md"
    saved.write_text("<!-- query: Смартфон до 200 000, Алматы! -->\n# Смартфон\n", encoding="utf-8")
    screen = SavedScreen(list_reports=lambda: [saved])
    qtbot.addWidget(screen)

    screen.reload()

    assert screen.reports.item(0).text() == "21.10.2026 20:38 — Смартфон до 200 000, Алматы!"
    assert screen.view.toPlainText().strip() == "Смартфон"


def test_no_reports_says_how_they_appear(qtbot):
    screen = SavedScreen(list_reports=lambda: [])
    qtbot.addWidget(screen)
    screen.reload()
    assert not screen.empty.isHidden() and not screen.folder_button.isEnabled()


def test_a_rendered_report_takes_the_theme_sizes_and_link_color(qapp):
    from PySide6.QtGui import QTextDocument, QTextFormat

    from buysmarter.gui.screens.saved import style_report
    from buysmarter.gui.theme import DARK, TYPE

    doc = QTextDocument()
    doc.setMarkdown("# Отчёт\n\n## Запрос\n\n[Kaspi](https://kaspi.kz/p/1)\n")
    style_report(doc, DARK)
    blocks = []
    block = doc.begin()
    while block.isValid():
        blocks.append(block.begin().fragment().charFormat())
        block = block.next()
    title, section, link = blocks
    assert not title.hasProperty(QTextFormat.Property.FontSizeAdjustment)
    assert title.intProperty(QTextFormat.Property.FontPixelSize) == TYPE["title"]
    assert section.intProperty(QTextFormat.Property.FontPixelSize) == TYPE["subhead"]
    assert link.isAnchor() and link.foreground().color().name().upper() == DARK["accent"].upper()
