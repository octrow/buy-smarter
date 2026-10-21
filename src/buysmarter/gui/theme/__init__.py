"""The app's look, "lab report": cool ink neutrals, white protocol cards, one teal accent.

One palette dict per scheme feeds a generated QSS, so every color is a named token and the
contrast test checks every text/background pair. The scheme follows the OS and re-applies live.
"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)

_ASSETS = Path(__file__).parent
_FONT_FILE = _ASSETS / "fonts" / "InterVariable.ttf"
FONT_FAMILY = "Inter Variable"  # the family name InterVariable.ttf registers

# Type: one family, ratio ~1.2 from a 14px body (operate UI: fixed px, not fluid).
TYPE = {"caption": 12, "body": 14, "subhead": 16, "title": 20, "display": 24}
# Spacing: a 4px grid.
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = 6

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

QListWidget#sidebar {{ background: {p["sidebar"]}; border: none; border-right: 1px solid {p["border"]}; padding: {s["md"]}px {s["sm"]}px; outline: 0; }}
QListWidget#sidebar::item {{ padding: {s["sm"]}px {s["md"]}px; border-radius: {r}px; color: {p["text_muted"]}; margin-bottom: 2px; }}
QListWidget#sidebar::item:hover {{ background: {p["border"]}; color: {p["text"]}; }}
QListWidget#sidebar::item:selected {{ background: {p["accent_soft"]}; color: {p["text"]}; font-weight: 600; }}

QGroupBox {{ background: {p["surface"]}; border: 1px solid {p["border"]}; border-radius: {r + 2}px; margin-top: {s["xl"]}px; padding: {s["lg"]}px {s["lg"]}px {s["md"]}px; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 2px; padding: 0 0 {s["xs"]}px 0; color: {p["text"]}; font-size: {t["subhead"]}px; font-weight: 600; }}
QGroupBox QLabel, QGroupBox QWidget {{ background: transparent; }}

QLabel[role="muted"] {{ color: {p["text_muted"]}; }}
QLabel[role="caption"] {{ color: {p["text_muted"]}; font-size: {t["caption"]}px; }}
QLabel#bestTitle {{ font-size: {t["title"]}px; font-weight: 600; }}
QLabel#bestScore {{ font-size: {t["display"]}px; font-weight: 700; color: {p["accent"]}; }}
QLabel#bestPrice {{ font-size: {t["display"]}px; font-weight: 600; }}
QLabel[state="done"], QLabel[state="ok"] {{ color: {p["ok"]}; font-weight: 600; }}
QLabel[state="running"] {{ color: {p["accent"]}; font-weight: 600; }}
QLabel[state="failed"], QLabel[state="missing"] {{ color: {p["danger"]}; font-weight: 600; }}
QLabel[state="skipped"], QLabel[state="waiting"] {{ color: {p["text_muted"]}; }}
QLabel[state="warning"] {{ color: {p["warning"]}; font-weight: 600; }}
QLabel[state="error"] {{ color: {p["danger"]}; }}

QLineEdit, QSpinBox, QComboBox {{ background: {p["surface"]}; border: 1px solid {p["border_strong"]}; border-radius: {r}px; padding: {s["sm"] - 2}px {s["sm"]}px; selection-background-color: {p["accent_soft"]}; selection-color: {p["text"]}; min-height: 20px; }}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {p["text_muted"]}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 2px solid {p["accent"]}; padding: {s["sm"] - 3}px {s["sm"] - 1}px; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {p["disabled"]}; background: {p["window"]}; border-color: {p["border"]}; }}
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
QCheckBox:focus {{ color: {p["accent"]}; }}

QTableView {{ background: {p["surface"]}; alternate-background-color: {p["window"]}; border: 1px solid {p["border"]}; border-radius: {r}px; gridline-color: {p["border"]}; selection-background-color: {p["accent_soft"]}; selection-color: {p["text"]}; outline: 0; }}
QTableView::item {{ padding: {s["xs"]}px {s["sm"]}px; }}
QTableView::item:hover {{ background: {p["sidebar"]}; }}
QHeaderView::section {{ background: {p["sidebar"]}; color: {p["text_muted"]}; border: none; border-bottom: 1px solid {p["border"]}; border-right: 1px solid {p["border"]}; padding: {s["sm"] - 2}px {s["sm"]}px; font-size: {t["caption"]}px; font-weight: 600; }}
QTableCornerButton::section {{ background: {p["sidebar"]}; border: none; }}

QProgressBar {{ background: {p["sidebar"]}; border: none; border-radius: 3px; height: 6px; max-height: 6px; text-align: right; }}
QProgressBar::chunk {{ background: {p["accent"]}; border-radius: 3px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p["border_strong"]}; border-radius: 4px; min-height: 32px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p["border_strong"]}; border-radius: 4px; min-width: 32px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QMessageBox {{ background: {p["surface"]}; }}
"""


def palette_for(scheme: Qt.ColorScheme) -> dict[str, str]:
    return DARK if scheme == Qt.ColorScheme.Dark else LIGHT


def load_font() -> None:
    """Register the bundled Inter; the QSS falls back to the platform sans when it fails."""
    if QFontDatabase.addApplicationFont(str(_FONT_FILE)) < 0:
        logger.warning("Could not load the bundled font %s", _FONT_FILE)


def apply_theme(app: QApplication) -> None:
    """Load Inter, style the app for the OS scheme, and follow scheme changes live."""
    load_font()
    hints = QGuiApplication.styleHints()

    def restyle(scheme: Qt.ColorScheme) -> None:
        app.setStyleSheet(build_qss(palette_for(scheme)))

    restyle(hints.colorScheme())
    hints.colorSchemeChanged.connect(restyle)


def set_state(widget: QWidget, name: str, value: str) -> None:
    """Set a dynamic property the QSS keys on (``state``, ``role``, ``primary``) and re-style."""
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
