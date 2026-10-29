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

from buysmarter.cli.prefs import LABELS, NO_SOURCE, SAVED, STORE_DOWN
from buysmarter.gui.bridge import CoreRunner, Job
from buysmarter.gui.prefs_fields import (
    BUDGET,
    CITY,
    EXCLUDED_BRANDS,
    MARKETPLACES,
    as_budget,
    as_list,
    budget_box,
    check_sources,
    source_boxes,
    store_warning,
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
        )
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

    def values(self) -> dict[str, Any]:
        return {
            CITY: self.city.text().strip(),
            BUDGET: self.budget.value(),
            MARKETPLACES: [name for name, box in self.sources.items() if box.isChecked()],
            EXCLUDED_BRANDS: as_list(self.excluded.text()),
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
