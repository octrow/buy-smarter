"""Category learning, kept per category so a new report never cancels or breaks another's.

One ``CategoryLearning`` lives in the main window and is shared with the search and report
screens. A learn is either automatic (``learn_now``: the search stopped with ``NeedsLearning``
to learn a new category first, #26) or started by the "Изучить категорию" button.
"""

import time
from collections.abc import Callable, Coroutine
from typing import Any

from PySide6.QtCore import QObject, Signal

from buysmarter.gui.bridge import USER_ERROR, CoreRunner, Job
from buysmarter.knowledge.generate import learn_category
from buysmarter.services.auto_learn import auto_learn, count_start, should_auto_learn

LEARN_FAILED = "Не удалось изучить категорию. Подробности — в лог-файле."
LEARN_CANCELLED = "Изучение категории отменено."

type Learn = Callable[[str], Coroutine[Any, Any, Any]]


class CategoryLearning(QObject):
    """Learning jobs by category, and how the last one of each category ended."""

    started = Signal(str)  # category
    settled = Signal(str)  # category: its learn ended, see ``outcome``

    def __init__(self, runner: CoreRunner | None, learn: Learn = learn_category) -> None:
        super().__init__()
        self._runner = runner
        self._learn = learn
        self._jobs: dict[str, Job] = {}
        self._began: dict[str, float] = {}
        self._outcome: dict[str, str] = {}  # "" = learned, otherwise the failure message
        self._checking: set[str] = set()

    def running(self, category: str) -> bool:
        return category in self._jobs

    def any_running(self) -> bool:
        return bool(self._jobs)

    def elapsed_s(self, category: str) -> int:
        began = self._began.get(category)
        return 0 if began is None else int(time.monotonic() - began)

    def outcome(self, category: str) -> str | None:
        """``None`` while unknown, ``""`` when learned, else the failure message."""
        return self._outcome.get(category)

    def forget(self, category: str) -> None:
        """A fresh report of the category: its last outcome is stale (a running learn stays)."""
        if category not in self._jobs:
            self._outcome.pop(category, None)

    def start(self, category: str) -> None:
        """The manual path: learn the category with the injected ``learn``."""
        self._begin(category, self._learn(category))

    def auto_start(
        self, category: str, *, intent: str, use_llm: bool, knowledge_missing: bool
    ) -> None:
        """Learn the category in the background if decision 8 allows; the caller never waits."""
        if self._runner is None or category in self._jobs or category in self._checking:
            return
        self._checking.add(category)
        decision = self._runner.submit(
            should_auto_learn(
                category, intent=intent, use_llm=use_llm, knowledge_missing=knowledge_missing
            )
        )
        decision.finished.connect(lambda allowed: self._decided(category, bool(allowed)))
        decision.failed.connect(lambda _msg: self._checking.discard(category))
        decision.cancelled.connect(lambda: self._checking.discard(category))

    def _decided(self, category: str, allowed: bool) -> None:
        self._checking.discard(category)
        if allowed:
            self.learn_now(category)

    def learn_now(self, category: str) -> None:
        """Learn the category with ``auto_learn``: its condition already held."""
        if self._runner is None or category in self._jobs:
            return
        count_start()  # here, where it is decided: the runner does not count
        self._begin(category, auto_learn(category))

    def cancel(self, category: str) -> None:
        """Stop the category's learn; ``settled`` fires with ``LEARN_CANCELLED``."""
        if (job := self._jobs.get(category)) is not None:
            job.cancel()

    def _begin(self, category: str, work: Coroutine[Any, Any, Any]) -> None:
        if self._runner is None or category in self._jobs:
            work.close()
            return
        self._outcome.pop(category, None)
        job = self._runner.submit(work)
        self._jobs[category] = job
        self._began[category] = time.monotonic()
        # ``auto_learn`` answers False on a failure; the manual learn raises instead.
        job.finished.connect(
            lambda ok: self._settle(category, "" if ok is not False else LEARN_FAILED)
        )
        job.failed.connect(lambda _msg: self._settle(category, USER_ERROR))
        job.cancelled.connect(lambda: self._settle(category, LEARN_CANCELLED))
        self.started.emit(category)

    def _settle(self, category: str, outcome: str) -> None:
        self._jobs.pop(category, None)
        self._began.pop(category, None)
        self._outcome[category] = outcome
        self.settled.emit(category)
