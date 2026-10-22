"""The clarification card on the search screen: the question, one button per option, own text."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from buysmarter.gui.theme import SPACE, set_state
from buysmarter.services.understanding import Clarification

_OWN_PLACEHOLDER = "или свой ответ"
_ANSWER_TEXT = "Ответить"


class ClarificationBox(QFrame):
    """Emits ``answered(str)``: an option's text or the user's own words."""

    answered = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        self.warning = QLabel(wordWrap=True)
        set_state(self.warning, "state", "warning")
        self.question = QLabel(wordWrap=True)
        self.question.setObjectName("sectionTitle")
        self.own = QLineEdit(placeholderText=_OWN_PLACEHOLDER)
        self.own.returnPressed.connect(self._send_own)
        send = QPushButton(_ANSWER_TEXT)
        send.clicked.connect(self._send_own)
        self._options = QHBoxLayout()
        self._options.setSpacing(SPACE["sm"])
        self.option_buttons: list[QPushButton] = []
        own_row = QHBoxLayout()
        own_row.setSpacing(SPACE["sm"])
        own_row.addWidget(self.own, 1)
        own_row.addWidget(send)
        body = QVBoxLayout(self)
        body.setContentsMargins(SPACE["xl"], SPACE["lg"], SPACE["xl"], SPACE["lg"])
        body.setSpacing(SPACE["md"])
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
            button.clicked.connect(lambda _checked=False, text=option: self.answered.emit(text))
            self._options.addWidget(button)
            self.option_buttons.append(button)
        self.own.clear()
        self.show()

    def _send_own(self) -> None:
        if text := self.own.text().strip():
            self.answered.emit(text)
