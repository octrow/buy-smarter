"""The report's 👍/👎: a card's main photo becomes a style sample, as ``style like/dislike``.

Shown only where they can work (``cli.style.votes_work``). The texts are the CLI's
(``cli/style``), the call the core's (``style.profile.add_card``), so a vote from either
lands as the same sample.
"""

import logging
from collections.abc import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from buysmarter.cli.prefs import STORE_DOWN
from buysmarter.cli.style import MODEL_DOWN, NO_CARD_PHOTO, liked_line
from buysmarter.gui.bridge import CoreRunner
from buysmarter.gui.theme import SPACE, set_state
from buysmarter.ranking.models import Candidate
from buysmarter.style import profile, weights

logger = logging.getLogger(__name__)

LIKE_TEXT = "👍 В мой стиль"
DISLIKE_TEXT = "👎 Не мой стиль"

type Picked = Callable[[], Candidate | None]


async def _vote(candidate: Candidate, disliked: bool) -> str:
    """Add the card's photo as a sample; the line to show."""
    if not candidate.image_url:
        return NO_CARD_PHOTO
    try:
        sample = await profile.add_card(candidate.image_url, candidate.title, disliked)
    except profile.NoCardPhoto:
        return NO_CARD_PHOTO
    except weights.WeightsError as exc:
        logger.warning("Style model not downloaded: %r", exc)
        return MODEL_DOWN
    return liked_line(sample)


class StyleVotes(QWidget):
    """👍/👎 for whatever ``picked`` returns (the best pick, or the selected row)."""

    def __init__(self, runner: CoreRunner, picked: Picked) -> None:
        super().__init__()
        self._runner, self._picked = runner, picked
        self.message = QLabel(wordWrap=True)
        set_state(self.message, "role", "muted")
        self.like = QPushButton(LIKE_TEXT)
        self.dislike = QPushButton(DISLIKE_TEXT)
        self.like.clicked.connect(lambda: self._send(disliked=False))
        self.dislike.clicked.connect(lambda: self._send(disliked=True))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE["sm"])
        row.addWidget(self.like)
        row.addWidget(self.dislike)
        row.addWidget(self.message, 1)

    def set_enabled(self, enabled: bool) -> None:
        self.like.setEnabled(enabled)
        self.dislike.setEnabled(enabled)

    def _send(self, disliked: bool) -> None:
        if (candidate := self._picked()) is None:
            return
        self.set_enabled(False)
        job = self._runner.submit(_vote(candidate, disliked))
        job.finished.connect(self._done)
        job.failed.connect(lambda _m: self._done(STORE_DOWN))

    def _done(self, line: str) -> None:
        self.set_enabled(self._picked() is not None)
        self.message.setText(line)
