"""The clarification card on the search screen: the question, one button per option, own text."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from buysmarter.gui.theme import CARD_GAP, SPACE, Prose, set_state
from buysmarter.services.understanding import Clarification

_OWN_PLACEHOLDER = "или свой ответ"
_ANSWER_TEXT = "Ответить"
_OWN_NAME = "Свой ответ"
SKIP_TEXT = "Искать без уточнений"
_SKIP_HINT = "Искать первый вариант и больше не спрашивать (Enter на вопросе в CLI)."


class ClarificationBox(QFrame):
    """Emits ``answered(str)``: an option or the user's own words; ``skipped``: ask nothing more."""

    answered = Signal(str)
    skipped = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        self.warning = Prose()
        set_state(self.warning, "state", "warning")
        self.question = QLabel(wordWrap=True)
        self.question.setObjectName("sectionTitle")
        self.own = QLineEdit(placeholderText=_OWN_PLACEHOLDER)
        self.own.setAccessibleName(_OWN_NAME)
        self.own.returnPressed.connect(self._send_own)
        send = QPushButton(_ANSWER_TEXT)
        send.clicked.connect(self._send_own)
        self.skip_button = QPushButton(SKIP_TEXT)
        self.skip_button.setToolTip(_SKIP_HINT)
        self.skip_button.clicked.connect(self.skipped.emit)
        # One option per line: the LLM's options can be long, a row would widen the window.
        self._options = QVBoxLayout()
        self._options.setSpacing(SPACE["sm"])
        self.option_buttons: list[QPushButton] = []
        own_row = QHBoxLayout()
        own_row.setSpacing(SPACE["sm"])
        own_row.addWidget(self.own, 1)
        own_row.addWidget(send)
        own_row.addWidget(self.skip_button)
        body = QVBoxLayout(self)
        body.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])  # as every card
        body.setSpacing(CARD_GAP)
        body.addWidget(self.warning)
        body.addWidget(self.question)
        body.addLayout(self._options)
        body.addLayout(own_row)
        self.hide()

    def ask(self, clarification: Clarification) -> None:
        """Show ``clarification``, replacing whatever was asked before."""
        self.warning.setText(f"⚠ {clarification.restricted_note}")
        self.warning.setVisible(bool(clarification.restricted_note))
        self.question.setText(clarification.question)
        for button in self.option_buttons:  # out of the row now; deleted on the next loop turn
            self._options.removeWidget(button)
            button.hide()
            button.deleteLater()
        self.option_buttons = []
        for option in clarification.options:
            button = QPushButton(option)
            button.setObjectName("option")  # reads as a list of answers, not a stack of actions
            button.clicked.connect(lambda _checked=False, text=option: self.answered.emit(text))
            self._options.addWidget(button)
            self.option_buttons.append(button)
        self.own.clear()
        self.show()

    def _send_own(self) -> None:
        if text := self.own.text().strip():
            self.answered.emit(text)
