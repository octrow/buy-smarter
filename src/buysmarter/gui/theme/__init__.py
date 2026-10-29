"""The app's look, "lab report": cool ink neutrals, white protocol cards, one teal accent.

One palette dict per scheme feeds a generated QSS, so every color is a named token and the
contrast test checks every text/background pair. The scheme follows the OS and re-applies live.
"""

import logging
import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtGui import QFontDatabase, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFormLayout,
    QLabel,
    QLayout,
    QWidget,
)

os.environ.setdefault("QT_API", "pyside6")  # qtawesome loads Qt through qtpy: never another binding
import qtawesome  # noqa: E402 - needs QT_API first

logger = logging.getLogger(__name__)

_ASSETS = Path(__file__).parent
# Static instances of Inter 4.1 cut from InterVariable.ttf (opsz=14): Qt registers a variable
# font as one Regular style, so font-weight 600 rendered as 400 and 700 as a synthetic bold.
_FONT_FILES = [
    _ASSETS / "fonts" / f"Inter-{style}.ttf" for style in ("Regular", "SemiBold", "Bold")
]
ICON = _ASSETS / "icon.svg"  # the window, the taskbar and the installers' menu entries
FONT_FAMILY = "Inter"  # the family name the Inter-*.ttf files register

# Type: one family, ratio ~1.2 from a 14px body (operate UI: fixed px, not fluid).
TYPE = {"caption": 12, "body": 14, "subhead": 16, "title": 20, "display": 24, "hero": 28}
# Spacing: a 4px grid.
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
# The same rhythm on every screen. Between cards: no less than a card's own padding (xl), or
# the cards stop reading as separate units. Between rows inside a card: md.
PAGE_GAP = SPACE["xl"]
CARD_GAP = SPACE["md"]
# Prose lines stop at ~70 characters (rules: 40-70), however wide the window: 70 x 8px at 14px.
PROSE_MAX_PX = 70 * SPACE["sm"]
RADIUS = 6
# Field text boxes, sized so a line edit, a combo box and a spin box all come out 36px tall:
# Fusion adds 3px more frame around a spin box than around a line edit.
_CONTROL_TEXT_PX = 22
# What that comes to: a field, and the line a form label is centred on.
CONTROL_PX = 36
# A card's title sits inside it (the QGroupBox font styles it; children reset to body):
# this much top padding holds the 16px title and its gap.
_CARD_TITLE_BAND_PX = 52
# Fusion draws a group box title this far in from its rect: pulled back, it lines up with the card's content.
_CARD_TITLE_INSET_PX = 2
_SPIN_TEXT_PX = 19
# A hint in a spin box reads as a line edit's placeholder: the text at half alpha (Qt's #AARRGGBB).
_PLACEHOLDER_ALPHA = "80"
# The item delegate insets cell text this far past the QSS padding; headers add it to line up.
_CELL_TEXT_INSET_PX = 3
# Pages stop growing here: on a wide monitor a line of text or a table row stays readable.
CONTENT_MAX_WIDTH = 1200

LIGHT = {
    "window": "#F4F6F8",  # page ground
    "surface": "#FFFFFF",  # cards, inputs, tables
    "sidebar": "#E9EDF1",  # second neutral layer: sidebar, table headers
    "border": "#D3D9E0",
    "border_strong": "#848F9B",  # input outlines (3:1 non-text contrast)
    "text": "#16202B",
    "text_muted": "#4A5663",
    "disabled": "#5E6975",
    "accent": "#00758A",  # KZ sky blue, darkened for AA
    "accent_hover": "#005F70",
    "on_accent": "#FFFFFF",
    "accent_soft": "#DCEFF2",  # selection, current nav item
    "ok": "#1D7135",
    "warning": "#875200",
    "danger": "#B3261E",
    "check_icon": "check-light.svg",  # drawn in on_accent
}

DARK = {
    "window": "#11161C",
    "surface": "#192028",
    "sidebar": "#141A21",
    "border": "#2B3540",
    "border_strong": "#5E6B78",
    "text": "#E5EAF0",
    "text_muted": "#A2AEBB",
    "disabled": "#8592A0",
    "accent": "#3FB8CC",
    "accent_hover": "#68CAD9",
    "on_accent": "#08171B",
    "accent_soft": "#173A42",
    "ok": "#5CC27C",
    "warning": "#E0AE4C",
    "danger": "#F28B82",
    "check_icon": "check-dark.svg",
}

# Every (text, background) pair the QSS renders; the test holds each to WCAG AA 4.5:1.
TEXT_PAIRS = [
    *(
        (fg, bg)
        for fg in ("text", "text_muted", "disabled")
        for bg in ("window", "surface", "sidebar")
    ),
    *((fg, bg) for fg in ("ok", "warning", "danger") for bg in ("window", "surface")),
    ("text", "accent_soft"),
    ("on_accent", "accent"),
    ("on_accent", "accent_hover"),
    ("accent", "surface"),
    ("accent", "window"),
]


def build_qss(p: dict[str, str]) -> str:
    """The app stylesheet for palette ``p``."""
    s, t, r = SPACE, TYPE, RADIUS
    return f"""
* {{ font-family: "{FONT_FAMILY}"; font-size: {t["body"]}px; color: {p["text"]}; }}
QMainWindow, QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {p["window"]}; }}
QStackedWidget > QWidget {{ background: {p["window"]}; }}
QScrollArea {{ border: none; }}
QToolTip {{ background: {p["surface"]}; color: {p["text"]}; border: 1px solid {p["border"]}; padding: {s["xs"]}px {s["sm"]}px; }}

QFrame#side {{ background: {p["sidebar"]}; border: none; border-right: 1px solid {p["border"]}; }}
QFrame#side QLabel {{ background: transparent; }}
QLabel#brand {{ font-size: {t["subhead"]}px; font-weight: 700; }}
QListWidget#sidebar {{ background: transparent; border: none; padding: 0 {s["sm"]}px; outline: 0; }}
QListWidget#sidebar::item {{ padding: {s["sm"]}px {s["md"]}px; border-radius: {r}px; color: {p["text_muted"]}; margin-bottom: 2px; }}
QListWidget#sidebar::item:hover {{ background: {p["border"]}; color: {p["text"]}; }}
QListWidget#sidebar::item:selected {{ background: {p["accent_soft"]}; color: {p["text"]}; font-weight: 600; }}
QListWidget#sidebar::item:disabled {{ background: transparent; color: {p["text_muted"]}; font-size: {t["caption"]}px; font-weight: 600; padding: {s["lg"]}px {s["md"]}px {s["xs"]}px; }}

QGroupBox {{ background: {p["surface"]}; border: 1px solid {p["border"]}; border-radius: {r + 4}px; margin-top: 0; padding: {_CARD_TITLE_BAND_PX}px {s["xl"]}px {s["xl"]}px; font-size: {t["subhead"]}px; font-weight: 600; }}
QGroupBox QWidget {{ font-size: {t["body"]}px; font-weight: 400; }}
QGroupBox QPushButton {{ font-weight: 600; }}  /* the rule above outranks plain QPushButton */
QGroupBox::title {{ subcontrol-origin: padding; subcontrol-position: top left; left: {s["xl"] - _CARD_TITLE_INSET_PX}px; top: {s["lg"]}px; padding: 0; color: {p["text"]}; font-size: {t["subhead"]}px; font-weight: 600; }}
QGroupBox QLabel, QGroupBox QWidget {{ background: transparent; }}

QLabel#hero {{ font-size: {t["hero"]}px; font-weight: 700; }}
QLabel#sectionTitle {{ font-size: {t["subhead"]}px; font-weight: 600; }}
QFrame#card {{ background: {p["surface"]}; border: 1px solid {p["border"]}; border-radius: {r + 4}px; }}
QFrame#card QLabel, QFrame#card QCheckBox, QFrame#card QWidget#plain {{ background: transparent; }}
QFrame#recent {{ background: {p["surface"]}; border: 1px solid {p["border"]}; border-radius: {r + 2}px; }}
QFrame#recent:hover {{ border-color: {p["accent"]}; }}
QFrame#recent QLabel {{ background: transparent; }}
QPushButton[flat="true"] {{ background: transparent; border: 1px solid transparent; color: {p["accent"]}; }}
QPushButton[flat="true"]:hover {{ background: {p["accent_soft"]}; }}
QPushButton#option, QGroupBox QPushButton#option {{ text-align: left; font-weight: 400; }}
QPushButton#inlineLink {{ padding-left: 0; padding-right: 0; }}  /* starts on the text's vertical */
QPushButton#inlineLink:hover {{ background: transparent; text-decoration: underline; }}
QLabel[role="muted"] {{ color: {p["text_muted"]}; }}
QLabel[role="caption"] {{ color: {p["text_muted"]}; font-size: {t["caption"]}px; }}
QLabel#bestTitle {{ font-size: {t["title"]}px; font-weight: 600; }}
QLabel#bestScore {{ font-size: {t["display"]}px; font-weight: 700; color: {p["accent"]}; }}
QLabel#bestPrice, QLabel#bestConfidence {{ font-size: {t["display"]}px; font-weight: 600; }}
QLabel[state="done"], QLabel[state="ok"] {{ color: {p["ok"]}; font-weight: 600; }}
QLabel[state="running"] {{ color: {p["accent"]}; font-weight: 600; }}
QLabel[state="failed"], QLabel[state="missing"] {{ color: {p["danger"]}; font-weight: 600; }}
QLabel[state="skipped"], QLabel[state="waiting"] {{ color: {p["text_muted"]}; }}
QLabel[state="warning"] {{ color: {p["warning"]}; font-weight: 600; }}
QLabel[state="error"] {{ color: {p["danger"]}; }}

QLineEdit, QSpinBox, QComboBox {{ background: {p["surface"]}; border: 1px solid {p["border_strong"]}; border-radius: {r}px; padding: {s["sm"] - 2}px {s["sm"]}px; selection-background-color: {p["accent_soft"]}; selection-color: {p["text"]}; min-height: {_CONTROL_TEXT_PX}px; }}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {p["text_muted"]}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 2px solid {p["accent"]}; padding: {s["sm"] - 3}px {s["sm"] - 1}px; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {p["disabled"]}; background: {p["window"]}; border-color: {p["border"]}; }}
QSpinBox {{ min-height: {_SPIN_TEXT_PX}px; }}
QSpinBox[state="hint"], QSpinBox[state="hint"] QLineEdit {{ color: #{_PLACEHOLDER_ALPHA}{p["text"][1:]}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
QComboBox QAbstractItemView {{ background: {p["surface"]}; border: 1px solid {p["border"]}; selection-background-color: {p["accent_soft"]}; selection-color: {p["text"]}; outline: 0; }}

QPushButton {{ background: {p["surface"]}; border: 1px solid {p["border_strong"]}; border-radius: {r}px; padding: {s["sm"]}px {s["lg"]}px; font-weight: 600; }}
QPushButton:hover {{ background: {p["sidebar"]}; }}
QPushButton:pressed {{ background: {p["border"]}; }}
QPushButton:focus {{ border: 2px solid {p["accent"]}; padding: {s["sm"] - 1}px {s["lg"] - 1}px; }}
QPushButton:disabled {{ color: {p["disabled"]}; background: {p["window"]}; border-color: {p["border"]}; }}
QPushButton[primary="true"] {{ background: {p["accent"]}; color: {p["on_accent"]}; border: 1px solid {p["accent"]}; }}
QPushButton[primary="true"]:hover {{ background: {p["accent_hover"]}; border-color: {p["accent_hover"]}; }}
QPushButton[primary="true"]:focus {{ border: 2px solid {p["text"]}; }}
QPushButton[primary="true"]:disabled {{ background: {p["window"]}; color: {p["disabled"]}; border-color: {p["border"]}; }}

QCheckBox {{ spacing: {s["sm"]}px; background: transparent; }}
QCheckBox:disabled {{ color: {p["disabled"]}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {p["border_strong"]}; border-radius: 4px; background: {p["surface"]}; }}
QCheckBox::indicator:hover {{ border-color: {p["accent"]}; }}
QCheckBox::indicator:checked {{ background: {p["accent"]}; border-color: {p["accent"]}; image: url("{(_ASSETS / p["check_icon"]).as_posix()}"); }}
QCheckBox::indicator:disabled {{ background: {p["window"]}; border-color: {p["border"]}; }}
QCheckBox::indicator:checked:disabled {{ background: {p["disabled"]}; border-color: {p["disabled"]}; }}
QCheckBox:focus {{ color: {p["accent"]}; }}

QTableView {{ background: {p["surface"]}; alternate-background-color: {p["window"]}; border: 1px solid {p["border"]}; border-radius: {r}px; selection-background-color: {p["accent_soft"]}; selection-color: {p["text"]}; outline: 0; }}
QTableView::item {{ padding: {s["sm"]}px {s["md"]}px; border-bottom: 1px solid {p["border"]}; }}
QTableView::item:hover {{ background: {p["sidebar"]}; }}
QHeaderView::section {{ background: {p["sidebar"]}; color: {p["text_muted"]}; border: none; border-bottom: 1px solid {p["border"]}; padding: {s["sm"]}px {s["md"]}px {s["sm"]}px {s["md"] + _CELL_TEXT_INSET_PX}px; font-size: {t["caption"]}px; font-weight: 600; }}
QTableCornerButton::section {{ background: {p["sidebar"]}; border: none; }}
/* Fusion draws the descending ("down-arrow") indicator as ▲; people read ▲ as ascending. */
QHeaderView::up-arrow {{ image: url("{(_ASSETS / "arrow-up.svg").as_posix()}"); width: 10px; height: 10px; }}
QHeaderView::down-arrow {{ image: url("{(_ASSETS / "arrow-down.svg").as_posix()}"); width: 10px; height: 10px; }}

QProgressBar {{ background: {p["border"]}; border: none; border-radius: 4px; height: 8px; max-height: 8px; text-align: right; }}
QProgressBar::chunk {{ background: {p["accent"]}; border-radius: 4px; }}
QProgressBar[level="low"]::chunk {{ background: {p["warning"]}; }}
QProgressBar[level="high"]::chunk {{ background: {p["ok"]}; }}
QFrame#banner {{ background: {p["accent_soft"]}; border: 1px solid {p["accent"]}; border-radius: {r + 2}px; }}
QFrame#banner QLabel {{ background: transparent; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p["border_strong"]}; border-radius: 4px; min-height: 32px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p["border_strong"]}; border-radius: 4px; min-width: 32px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QMessageBox {{ background: {p["surface"]}; }}
QTextBrowser {{ background: {p["surface"]}; border: 1px solid {p["border"]}; border-radius: {r}px; padding: {s["md"]}px; }}
QListWidget {{ background: {p["surface"]}; border: 1px solid {p["border"]}; border-radius: {r}px; outline: 0; }}
QListWidget::item {{ padding: {s["sm"]}px; }}
QListWidget::item:selected {{ background: {p["accent_soft"]}; color: {p["text"]}; }}
"""


def palette_for(scheme: Qt.ColorScheme) -> dict[str, str]:
    return DARK if scheme == Qt.ColorScheme.Dark else LIGHT


def current_palette() -> dict[str, str]:
    return palette_for(QGuiApplication.styleHints().colorScheme())


def icon(name: str, color: str = "text_muted") -> QIcon:
    """A Material Design icon (``mdi6.*``) in a palette color, accent when selected or hovered.

    Icons are drawn for the current scheme; a widget re-creates them on ``colorSchemeChanged``.
    """
    p = current_palette()
    drawn: QIcon = qtawesome.icon(
        name,
        color=p[color],
        color_active=p["accent"],
        color_selected=p["accent"],
        color_disabled=p["disabled"],
    )
    return drawn


class _IconBinding(QObject):
    """Keeps one widget's icon in the current scheme's colors; dies with its owner."""

    def __init__(
        self, owner: QObject, setter: Callable[[QIcon], object], name: str, color: str
    ) -> None:
        super().__init__(owner)  # a child: Qt disconnects it when the owner goes
        self._setter, self._name, self._color = setter, name, color
        self.repaint()
        QGuiApplication.styleHints().colorSchemeChanged.connect(self.repaint)

    @Slot()
    def repaint(self) -> None:
        self._setter(icon(self._name, self._color))


def bind_icon(
    owner: QObject, setter: Callable[[QIcon], object], name: str, color: str = "text_muted"
) -> None:
    """Set ``owner``'s icon through ``setter`` now and again on every OS scheme change."""
    _IconBinding(owner, setter, name, color)


def load_font() -> None:
    """Register the bundled Inter; the QSS falls back to the platform sans when it fails."""
    for path in _FONT_FILES:
        if QFontDatabase.addApplicationFont(str(path)) < 0:
            logger.warning("Could not load the bundled font %s", path)


def apply_theme(app: QApplication) -> None:
    """Load Inter, style the app for the OS scheme, and follow scheme changes live."""
    load_font()
    hints = QGuiApplication.styleHints()

    def restyle(scheme: Qt.ColorScheme) -> None:
        app.setStyleSheet(build_qss(palette_for(scheme)))

    restyle(hints.colorScheme())
    hints.colorSchemeChanged.connect(restyle)


class Prose(QLabel):
    """A paragraph: wraps, and stops at ``PROSE_MAX_PX`` however wide the card.

    For text that stands on its own line; a status beside buttons stays a plain QLabel, or the
    space it gives up stretches the buttons.
    """

    def __init__(self, text: str = "") -> None:
        super().__init__(text, wordWrap=True)
        self.setMaximumWidth(PROSE_MAX_PX)

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt's name
        # QLabel measures at the width it is offered, even past its maximum, and the layout
        # then makes room for fewer lines than the capped label wraps to.
        return super().heightForWidth(min(width, self.maximumWidth()))


def card_body[L: QLayout](layout: L) -> L:
    """A card's (QGroupBox's) layout without the style's own margins: the QSS padding frames the card.

    Rows inside are ``CARD_GAP`` apart, the same in every card.
    """
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(CARD_GAP)
    return layout


def form_rows(*rows: tuple[str, QWidget | QLayout]) -> QFormLayout:
    """A settings form: each label left of its field, centred on the field's first line.

    Qt gives a form label less height than the row and pins it to the top: 4px above the
    field's text. A label as tall as a field centres like the field's own text.
    """
    form = QFormLayout()
    form.setHorizontalSpacing(SPACE["lg"])
    form.setVerticalSpacing(CARD_GAP)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    for text, field in rows:
        label = QLabel(text)
        if text:  # an empty one only indents a caption row: no height of its own
            label.setMinimumHeight(CONTROL_PX)
            if isinstance(field, QBoxLayout):  # a row of check boxes centres on the label too
                field.addStrut(CONTROL_PX)
        if isinstance(field, QWidget):
            label.setBuddy(field)
        form.addRow(label, field)
    return form


def set_state(widget: QWidget, name: str, value: str) -> None:
    """Set a dynamic property the QSS keys on (``state``, ``role``, ``primary``) and re-style."""
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
