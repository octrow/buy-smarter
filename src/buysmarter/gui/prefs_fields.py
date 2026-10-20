"""The preference keys the app reads and writes, their parsing, and their shared form widgets."""

from typing import Any

from PySide6.QtWidgets import QCheckBox, QSpinBox

from buysmarter.services.collect import ALL_SOURCES

CITY = "city"  # the core reads this one too (services.recommend.resolve_city)
BUDGET = "budget"
MARKETPLACES = "marketplaces"
EXCLUDED_BRANDS = "excluded_brands"

SOURCE_LABELS = {"kaspi": "Kaspi", "ozon": "Ozon", "olx": "OLX"}
BUDGET_MAX = 100_000_000
_BUDGET_STEP = 1_000


def as_list(value: Any) -> list[str]:
    """A stored list or comma-separated string as clean, non-empty items."""
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list):
        return []
    return [text for item in items if (text := str(item).strip())]


def as_budget(value: Any) -> int:
    """A stored budget (int or "40 000") in KZT; 0 means no limit."""
    digits = str(value or "").replace(" ", "")
    return min(int(digits), BUDGET_MAX) if digits.isdigit() else 0


def budget_box() -> QSpinBox:
    box = QSpinBox(maximum=BUDGET_MAX, singleStep=_BUDGET_STEP, suffix=" ₸")
    box.setSpecialValueText("без лимита")
    box.setGroupSeparatorShown(True)
    return box


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
