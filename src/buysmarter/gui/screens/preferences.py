"""Preferences screen: the saved defaults every new search starts from."""

from collections.abc import Callable, Coroutine, Mapping
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from buysmarter.cli.prefs import BODY_NOTE, LABELS, NO_SOURCE, SAVED, STORE_DOWN
from buysmarter.gui.bridge import CoreRunner, Job
from buysmarter.gui.prefs_fields import (
    BANK_MARGIN,
    BANK_MARGIN_MAX,
    BODY_TOPS,
    BUDGET,
    CARGO_RATE,
    CARGO_RATE_MAX,
    CITY,
    EXCLUDED_BRANDS,
    MARKETPLACES,
    as_budget,
    as_list,
    body_of,
    budget_box,
    check_sources,
    measure_box,
    number_box,
    source_boxes,
    store_warning,
    tariffs_of,
)
from buysmarter.gui.theme import PAGE_GAP, SPACE, card_body, form_rows, set_state
from buysmarter.memory import prefs

type Store = Callable[[Mapping[str, Any]], Coroutine[Any, Any, None]]


async def save_all(values: Mapping[str, Any], store: Store) -> dict[str, Any]:
    await store(values)  # one transaction: a failure keeps none of the new values
    return dict(values)


class PreferencesScreen(QWidget):
    """Edits the stored preferences; emits ``saved(dict)`` once they are written."""

    saved = Signal(dict)

    def __init__(self, runner: CoreRunner, store: Store = prefs.set_many) -> None:
        super().__init__()
        self._runner = runner
        self._store = store
        self._job: Job | None = None
        self.city = QLineEdit(placeholderText="Алматы")
        self.budget = budget_box()
        self.sources = source_boxes()
        self.excluded = QLineEdit(placeholderText="через запятую, например: Noname, Generic")
        self.bank_margin = number_box(BANK_MARGIN_MAX, " %")
        self.cargo_rate = number_box(CARGO_RATE_MAX, " $")
        self._show_tariffs({})  # not 0 before the saved values arrive: saving then keeps them
        self.body = {key: measure_box(key) for key in BODY_TOPS}
        self.message = QLabel(wordWrap=True)  # beside the button: a capped width would stretch it
        self.store_warning = store_warning()
        self.save_button = QPushButton("Сохранить", default=True)
        set_state(self.save_button, "primary", "true")
        set_state(self.message, "role", "muted")
        self.save_button.clicked.connect(self.save)

        marketplaces = QHBoxLayout()
        marketplaces.setSpacing(SPACE["xl"])
        for box in self.sources.values():
            marketplaces.addWidget(box)
        marketplaces.addStretch(1)
        form = form_rows(
            (LABELS[CITY], self.city),
            (LABELS[BUDGET], self.budget),
            (LABELS[MARKETPLACES], marketplaces),
            (LABELS[EXCLUDED_BRANDS], self.excluded),
            (LABELS[BANK_MARGIN], self.bank_margin),
            (LABELS[CARGO_RATE], self.cargo_rate),
            *((LABELS[key], box) for key, box in self.body.items()),
        )
        body_note = QLabel(BODY_NOTE, wordWrap=True)
        set_state(body_note, "role", "caption")
        layout = QVBoxLayout(self)
        # Under the screen heading (app._centered): no top gap of its own.
        layout.setContentsMargins(SPACE["xxl"], SPACE["lg"], SPACE["xxl"], SPACE["xl"])
        layout.setSpacing(PAGE_GAP)
        buttons = QHBoxLayout()
        buttons.addWidget(self.save_button)
        buttons.addSpacing(SPACE["md"])
        buttons.addWidget(self.message, 1)  # beside the button: an empty row would pad the card
        card = QGroupBox("Для каждого поиска")
        inside = card_body(QVBoxLayout(card))
        inside.addLayout(form)
        inside.addWidget(body_note)
        inside.addLayout(buttons)
        layout.addWidget(self.store_warning)
        layout.addWidget(card)
        layout.addStretch(1)

    def apply_prefs(self, stored: Mapping[str, Any]) -> None:
        # A city the user typed wins over a default arriving later.
        if isinstance(city := stored.get(CITY), str) and not self.city.isModified():
            self.city.setText(city)
        self.budget.setValue(as_budget(stored.get(BUDGET)))
        check_sources(self.sources, stored.get(MARKETPLACES))
        self.excluded.setText(", ".join(as_list(stored.get(EXCLUDED_BRANDS))))
        self._show_tariffs(stored)
        body = body_of(stored)
        for key, box in self.body.items():
            box.setValue(getattr(body, key) or 0)

    def _show_tariffs(self, stored: Mapping[str, Any]) -> None:
        tariffs = tariffs_of(stored)  # unset shows the default the landed cost uses
        self.bank_margin.setValue(tariffs.bank_margin * 100)
        self.cargo_rate.setValue(tariffs.cargo_usd_per_kg)

    def values(self) -> dict[str, Any]:
        return {
            CITY: self.city.text().strip(),
            BUDGET: self.budget.value(),
            MARKETPLACES: [name for name, box in self.sources.items() if box.isChecked()],
            EXCLUDED_BRANDS: as_list(self.excluded.text()),
            BANK_MARGIN: round(self.bank_margin.value(), 1),
            CARGO_RATE: round(self.cargo_rate.value(), 1),
            **{key: box.value() or None for key, box in self.body.items()},
        }

    def save(self) -> None:
        if self._job is not None:
            return
        values = self.values()
        if not values[MARKETPLACES]:
            self.message.setText(NO_SOURCE)
            return
        self.save_button.setEnabled(False)
        self.message.clear()
        self._job = self._runner.submit(save_all(values, self._store))
        self._job.finished.connect(self._on_saved)
        self._job.failed.connect(self._on_failed)

    def _on_saved(self, values: dict[str, Any]) -> None:
        self._job = None
        self.save_button.setEnabled(True)
        self.message.setText(SAVED)
        self.saved.emit(values)

    def _on_failed(self, _message: str) -> None:
        self._job = None
        self.save_button.setEnabled(True)
        self.message.setText(STORE_DOWN)
