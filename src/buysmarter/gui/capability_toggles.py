"""«Как оцениваем эту категорию»: the user's own switch for each category capability.

One checkbox per category-level capability with the source in its text ("Состав — включено
обучением"), so the state never rests on colour alone, and a reset button that returns the
capability to its automatic value. A switch is written to the profile of the request's own
category (``set_user_choice``; the related profile the lookup found becomes its parent, and is
never written to), on the core loop. Nothing is searched again by itself: a change only offers
the rerun (phase 3, decision 13).
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from buysmarter.capabilities.provenance import CapabilityState, toggle_text
from buysmarter.gui.bridge import USER_ERROR, CoreRunner
from buysmarter.gui.theme import SPACE, card_body, set_state
from buysmarter.knowledge.store import set_user_choice

TITLE = "Как оцениваем эту категорию"
RESET_TEXT = "Сбросить к автоматическому"
RERUN_TEXT = "Повторить поиск"
CHANGED_NOTE = "Изменения сработают при следующем поиске."
NO_RUNNER_HINT = "Недоступно: нет связи с ядром."


class CapabilityToggles(QGroupBox):
    """The switches of one report's category; ``rerun_requested``: the user wants a new search."""

    rerun_requested = Signal()

    def __init__(
        self,
        runner: CoreRunner | None,
        category: str,
        found: str | None,
        states: list[CapabilityState],
    ) -> None:
        super().__init__(TITLE)
        self._runner = runner
        self._category = category
        self._found = found
        self._states = {s.name: s for s in states if s.level == "category"}
        self.checkboxes: dict[str, QCheckBox] = {}
        self.resets: dict[str, QPushButton] = {}
        layout = card_body(QVBoxLayout(self))
        layout.setSpacing(SPACE["sm"])
        for name in self._states:
            layout.addWidget(self._build_row(name))
        layout.addLayout(self._build_footer())
        for name in self._states:
            self._render(name)

    def _build_footer(self) -> QHBoxLayout:
        """The note and the rerun button shown after a change, hidden until then."""
        self.note = QLabel(wordWrap=True)
        self.note.setProperty("role", "caption")
        self.rerun_button = QPushButton(RERUN_TEXT)
        set_state(self.rerun_button, "primary", "true")
        self.rerun_button.clicked.connect(self.rerun_requested)
        footer = QHBoxLayout()
        footer.setSpacing(SPACE["md"])
        footer.addWidget(self.note, 1)
        footer.addWidget(self.rerun_button)
        self.note.hide()
        self.rerun_button.hide()
        return footer

    def _build_row(self, name: str) -> QWidget:
        box, reset = QCheckBox(), QPushButton(RESET_TEXT)
        box.clicked.connect(lambda checked, n=name: self._write(n, checked))
        reset.clicked.connect(lambda _=False, n=name: self._write(n, None))
        self.checkboxes[name], self.resets[name] = box, reset
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["md"])
        layout.addWidget(box, 1)
        layout.addWidget(reset)
        return row

    def _render(self, name: str) -> None:
        state, box, reset = self._states[name], self.checkboxes[name], self.resets[name]
        text = toggle_text(state)
        box.setText(text)
        box.setChecked(state.on)
        box.setAccessibleName(text)
        reset.setAccessibleName(f"{RESET_TEXT}: {state.label}")
        enabled = self._runner is not None
        box.setEnabled(enabled)
        reset.setEnabled(enabled and state.user is not None)
        if not enabled:
            box.setToolTip(NO_RUNNER_HINT)

    def _write(self, name: str, choice: bool | None) -> None:
        """Save the choice on the core loop; the row is locked until it is stored."""
        if self._runner is None:
            return
        self.checkboxes[name].setEnabled(False)
        self.resets[name].setEnabled(False)
        job = self._runner.submit(set_user_choice(self._category, name, choice, parent=self._found))
        job.finished.connect(lambda _=None: self._stored(name, choice))
        job.failed.connect(lambda _msg: self._not_stored(name))
        job.cancelled.connect(lambda: self._not_stored(name))

    def _stored(self, name: str, choice: bool | None) -> None:
        state = self._states[name]
        on = state.auto if choice is None else choice
        self._states[name] = state.model_copy(
            update={"user": choice, "on": on, "active": on and (state.active or state.auto)}
        )
        self._render(name)
        self.note.setText(CHANGED_NOTE)
        self.note.show()
        self.rerun_button.show()

    def _not_stored(self, name: str) -> None:
        self._render(name)  # back to what is stored
        self.note.setText(USER_ERROR)
        self.note.show()
