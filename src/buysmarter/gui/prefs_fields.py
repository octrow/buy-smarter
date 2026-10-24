"""The app's shared preference form widgets; keys and parsing come from ``memory.prefs``."""

from typing import Any

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QCheckBox, QLabel, QSpinBox

from buysmarter.gui.theme import SPACE, set_state
from buysmarter.i18n import SOURCE_LABELS
from buysmarter.i18n import kzt as kzt  # re-exported: the screens format prices through here

# The keys and their parsing live with the store (the CLI reads them too); re-exported here.
from buysmarter.memory.prefs import BUDGET as BUDGET
from buysmarter.memory.prefs import BUDGET_MAX, as_sources
from buysmarter.memory.prefs import CITY as CITY
from buysmarter.memory.prefs import EXCLUDED_BRANDS as EXCLUDED_BRANDS
from buysmarter.memory.prefs import MARKETPLACES as MARKETPLACES
from buysmarter.memory.prefs import as_budget as as_budget
from buysmarter.memory.prefs import as_list as as_list
from buysmarter.services.collect import ALL_SOURCES

# Russian digit grouping, "40 000 ₸" (a no-break space), as ``i18n.kzt`` writes prices.
MONEY_LOCALE = QLocale(QLocale.Language.Russian, QLocale.Country.Kazakhstan)
_BUDGET_STEP = 1_000
STORE_OFF = (
    "Хранилище выключено: настройки не сохранятся, истории цен нет. "
    "Причина — на экране «Состояние и ключи»."
)


def budget_box() -> QSpinBox:
    box = QSpinBox(maximum=BUDGET_MAX, singleStep=_BUDGET_STEP, suffix=" ₸")
    box.setLocale(MONEY_LOCALE)
    box.setSpecialValueText("без лимита")
    box.setGroupSeparatorShown(True)
    # QSS horizontal padding does not reach a spin box's text: inset it like a QLineEdit's.
    box.lineEdit().setTextMargins(SPACE["sm"], 0, SPACE["sm"], 0)
    # 0 shows the special text ("без лимита", a hint): muted, as a line edit's placeholder.
    box.valueChanged.connect(lambda value: set_state(box, "state", "" if value else "hint"))
    set_state(box, "state", "hint")
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
    wanted = set(as_sources(stored)) & boxes.keys()
    if wanted:
        for name, box in boxes.items():
            box.setChecked(name in wanted)
