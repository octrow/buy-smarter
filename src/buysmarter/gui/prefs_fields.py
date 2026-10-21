"""The preference keys the app reads and writes, their parsing, and their shared form widgets."""

from typing import Any

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QCheckBox, QLabel, QSpinBox

from buysmarter.gui.theme import SPACE, set_state
from buysmarter.i18n import SOURCE_LABELS
from buysmarter.i18n import kzt as kzt  # re-exported: the screens format prices through here
from buysmarter.services.collect import ALL_SOURCES

CITY = "city"  # the core reads this one too (services.recommend.resolve_city)
BUDGET = "budget"
MARKETPLACES = "marketplaces"
EXCLUDED_BRANDS = "excluded_brands"

BUDGET_MAX = 100_000_000
# Russian digit grouping, "40 000 ₸" (a no-break space), as ``i18n.kzt`` writes prices.
MONEY_LOCALE = QLocale(QLocale.Language.Russian, QLocale.Country.Kazakhstan)
_BUDGET_STEP = 1_000
STORE_OFF = (
    "Хранилище выключено: настройки не сохранятся, истории цен нет. "
    "Причина — на экране «Состояние и ключи»."
)


def as_list(value: Any) -> list[str]:
    """A stored list or comma-separated string as clean, non-empty items."""
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list):
        return []
    return [text for item in items if (text := str(item).strip())]


def as_budget(value: Any) -> int:
    """A stored budget (int or "40 000") in KZT; 0 means no limit."""
    digits = "".join(str(value or "").split())  # also drops the no-break space of "40 000"
    return min(int(digits), BUDGET_MAX) if digits.isdigit() else 0


def budget_box() -> QSpinBox:
    box = QSpinBox(maximum=BUDGET_MAX, singleStep=_BUDGET_STEP, suffix=" ₸")
    box.setLocale(MONEY_LOCALE)
    box.setSpecialValueText("без лимита")
    box.setGroupSeparatorShown(True)
    # QSS horizontal padding does not reach a spin box's text: inset it like a QLineEdit's.
    box.lineEdit().setTextMargins(SPACE["sm"], 0, SPACE["sm"], 0)
    return box


def store_warning() -> QLabel:
    """Hidden until the health checks find the store off (``setVisible(True)``)."""
    label = QLabel(STORE_OFF, wordWrap=True)
    set_state(label, "state", "warning")
    label.hide()
    return label


def checked_box(text: str) -> QCheckBox:
    box = QCheckBox(text)
    box.setChecked(True)
    return box


def source_boxes() -> dict[str, QCheckBox]:
    return {name: checked_box(SOURCE_LABELS[name]) for name in ALL_SOURCES}


def check_sources(boxes: dict[str, QCheckBox], stored: Any) -> None:
    """Tick exactly the stored marketplaces; leave the boxes alone when none of them is known."""
    wanted = {m.lower() for m in as_list(stored)} & boxes.keys()
    if wanted:
        for name, box in boxes.items():
            box.setChecked(name in wanted)
