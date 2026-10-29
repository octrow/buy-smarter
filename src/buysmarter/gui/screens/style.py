"""«Стиль»: the clothing-taste profile, as ``buysmarter style`` (plan В9).

Brands, phrases «нравится»/«нет» and sample photos; the texts are the CLI's (``cli/style``),
the calls the core's (``style.profile``), so either entry point sees what the other saved.
Every change is stored at once; a job answers with the line to show.
"""

import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImageReader, QPixmap, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from buysmarter import style
from buysmarter.cli.prefs import STORE_DOWN
from buysmarter.cli.style import (
    DOWNLOADING,
    LIKES,
    MODEL_DOWN,
    MODEL_READY,
    NEVERS,
    NO_EXTRA,
    NOT_FOUND,
    PHOTOS,
    PHRASE_HINT,
    SAVED,
    STOPPED,
    STRENGTH,
    UNTRANSLATED,
    added_line,
    model_line,
    photo_count,
    phrase_line,
)
from buysmarter.gui.bridge import CoreRunner, Job
from buysmarter.gui.theme import PAGE_GAP, SPACE, Prose, card_body, form_rows, set_state
from buysmarter.memory.prefs import as_list
from buysmarter.style import profile, weights

logger = logging.getLogger(__name__)

_PRIVATE = "Фото остаются на этом компьютере."
_STRENGTH_HINT = (
    f"вес оси «Стиль» в поиске одежды: {profile.STRENGTH_DEFAULT} — по умолчанию, 0 — выключено"
)
ADDING = "Добавляю фото… В первый раз скачивается модель стиля (~200 МБ)."
_THUMB_PX = 96
_PHOTOS_MIN_PX = 240  # about two rows of thumbnails
_PHRASES_MIN_PX = 96
_PHOTO_FILTER = "Фото (*.jpg *.jpeg *.png *.webp)"
_KEY = Qt.ItemDataRole.UserRole  # an item's photo name or phrase text

type PickPhotos = Callable[[QWidget], list[str]]
type PickFolder = Callable[[QWidget], str]


def pick_photos(parent: QWidget) -> list[str]:
    """The photos the user picks, ``[]`` when cancelled (as ``style add FILES``)."""
    paths, _ = QFileDialog.getOpenFileNames(parent, "Фото одежды", "", _PHOTO_FILTER)
    return paths


def pick_folder(parent: QWidget) -> str:
    """The folder the user picks, ``""`` when cancelled (as ``style add FOLDER``)."""
    return QFileDialog.getExistingDirectory(parent, "Папка с фото одежды")


async def _download() -> str:
    try:
        await weights.download()
    except weights.WeightsError as exc:
        logger.warning("Style model not downloaded: %r", exc)
        return MODEL_DOWN
    return MODEL_READY


async def _phrase(text: str, never: bool) -> str:
    try:
        await profile.add_phrase(text, never=never)
    except profile.Untranslated:
        return UNTRANSLATED
    return SAVED


async def _photos(paths: list[Path], disliked: bool, progress: profile.Progress) -> str:
    try:
        added = await profile.add_photos(paths, disliked, progress)
    except weights.WeightsError as exc:
        logger.warning("Style model not downloaded: %r", exc)
        return MODEL_DOWN
    return added_line(added)


async def _remove(keys: list[str]) -> str:
    found = [await profile.remove(key) for key in keys]
    return SAVED if all(found) else NOT_FOUND


async def _strength(value: int) -> str:
    await profile.set_strength(value)
    return SAVED


async def _brands(text: str) -> str:
    await profile.set_brands(as_list(text))
    return SAVED


def _thumbnail(path: Path) -> QIcon:
    """A square tile cut from the photo's centre, decoded at that size (fast for a big JPEG)."""
    reader = QImageReader(str(path))
    if (size := reader.size()).isValid():
        fill = size.scaled(_THUMB_PX, _THUMB_PX, Qt.AspectRatioMode.KeepAspectRatioByExpanding)
        reader.setScaledSize(fill)
        left, top = (fill.width() - _THUMB_PX) // 2, (fill.height() - _THUMB_PX) // 2
        reader.setScaledClipRect(QRect(left, top, _THUMB_PX, _THUMB_PX))
    return QIcon(QPixmap.fromImage(reader.read()))


class _FitGrid(QListWidget):
    """Tall enough for every row of photos: the page scrolls, never the grid (as tables.py)."""

    def __init__(self) -> None:
        super().__init__()
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        model = self.model()
        for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            signal.connect(self._fit)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        cell = self.gridSize()
        per_row = max(1, self.viewport().width() // cell.width())
        rows = -(-self.count() // per_row)  # ceil
        self.setFixedHeight(max(_PHOTOS_MIN_PX, rows * cell.height() + 2 * self.frameWidth()))


def _photo_grid() -> QListWidget:
    grid = _FitGrid()
    grid.setViewMode(QListView.ViewMode.IconMode)
    grid.setIconSize(QSize(_THUMB_PX, _THUMB_PX))
    grid.setResizeMode(QListView.ResizeMode.Adjust)
    grid.setMovement(QListView.Movement.Static)
    # A fixed cell with room for the «Нет» caption under the photo.
    grid.setGridSize(QSize(_THUMB_PX + SPACE["md"], _THUMB_PX + SPACE["xl"]))
    grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    grid.setAccessibleName("Фото-образцы")
    return grid


def _caption(text: str) -> QLabel:
    label = QLabel(text, wordWrap=True)
    set_state(label, "role", "caption")
    return label


def _row(*widgets: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(SPACE["md"])
    for widget in widgets:
        row.addWidget(widget, 1 if isinstance(widget, QLineEdit | QLabel) else 0)
    return row


def _card(title: str) -> tuple[QGroupBox, QVBoxLayout]:
    card = QGroupBox(title)
    inside = card_body(QVBoxLayout(card))
    return card, inside


class StyleScreen(QWidget):
    """Edits the style profile: brands, phrases and sample photos."""

    _photo_progress = Signal(int, int)  # (done, total): the core loop thread -> the GUI thread

    def __init__(
        self,
        runner: CoreRunner,
        pick_photos: PickPhotos = pick_photos,
        pick_folder: PickFolder = pick_folder,
    ) -> None:
        super().__init__()
        self._runner = runner
        self._pick_photos = pick_photos
        self._pick_folder = pick_folder
        self.loaded = False  # the first read of the profile has arrived
        self.message = Prose()
        set_state(self.message, "role", "muted")
        self.message.hide()  # until there is something to say: no empty gap under the heading
        layout = QVBoxLayout(self)
        # Under the screen heading (app._centered): no top gap of its own.
        layout.setContentsMargins(SPACE["xxl"], SPACE["lg"], SPACE["xxl"], SPACE["xl"])
        layout.setSpacing(PAGE_GAP)
        layout.addWidget(self.message)
        layout.addWidget(self._build_settings())
        layout.addWidget(self._build_photos())
        layout.addWidget(self._build_phrases())
        layout.addStretch(1)  # the photo grid is as tall as its rows: spare height goes below

    def _build_photos(self) -> QGroupBox:
        self.photos = _photo_grid()
        self.no_extra = Prose(NO_EXTRA)
        set_state(self.no_extra, "state", "warning")
        self.photo_summary = _caption("")
        card, inside = _card("Образцы")
        inside.addWidget(self.photo_summary)
        inside.addWidget(self.no_extra)
        inside.addLayout(self._build_model_row())
        inside.addLayout(self._build_photo_buttons())
        inside.addLayout(self._build_progress_row())
        inside.addWidget(self.photos, 1)
        return card

    def _build_photo_buttons(self) -> QVBoxLayout:
        self.add_button = QPushButton("Добавить фото…")
        set_state(self.add_button, "primary", "true")
        self.add_folder_button = QPushButton("Добавить папку…")
        self.add_no_button = QPushButton(f"Фото в «{NEVERS.lower()}»…")
        self.add_no_folder_button = QPushButton(f"Папку в «{NEVERS.lower()}»…")
        self.remove_photo_button = QPushButton("Удалить")
        self.add_button.clicked.connect(lambda: self._add_photos(disliked=False))
        self.add_folder_button.clicked.connect(lambda: self._add_folder(disliked=False))
        self.add_no_button.clicked.connect(lambda: self._add_photos(disliked=True))
        self.add_no_folder_button.clicked.connect(lambda: self._add_folder(disliked=True))
        self.remove_photo_button.clicked.connect(self._remove_photos)
        liked = _row(self.add_button, self.add_folder_button, self.remove_photo_button)
        never = _row(self.add_no_button, self.add_no_folder_button)
        buttons = QVBoxLayout()
        buttons.setSpacing(SPACE["sm"])
        for row in (liked, never):
            row.addStretch(1)
            buttons.addLayout(row)
        return buttons

    def _build_progress_row(self) -> QHBoxLayout:
        """«N из M» while photos are added, with «Отмена»; hidden otherwise."""
        self.progress = QProgressBar(textVisible=False)  # the theme's bar is thin: count beside it
        self.progress.setAccessibleName("Добавление фото")
        self.progress_count = _caption("")
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.clicked.connect(self._cancel_adding)
        self._photo_progress.connect(self._on_progress)
        self._adding: Job | None = None
        self._show_progress(False)
        row = _row(self.progress, self.progress_count, self.cancel_button)
        row.setStretch(0, 1)
        row.setStretch(1, 0)
        return row

    def _build_model_row(self) -> QHBoxLayout:
        self.model_status = _caption("")
        self.download_button = QPushButton("Скачать модель")
        self.download_button.clicked.connect(self._download)
        return _row(self.model_status, self.download_button)

    def _build_phrases(self) -> QGroupBox:
        self.phrase = QLineEdit(placeholderText="например: белая глянцевая пластиковая подошва")
        self.phrase.setAccessibleName("Фраза")
        self.like_button = QPushButton(f"+ {LIKES}")
        self.never_button = QPushButton(f"+ {NEVERS}")
        self.like_button.clicked.connect(lambda: self._add_phrase(never=False))
        self.never_button.clicked.connect(lambda: self._add_phrase(never=True))
        self.phrases = QListWidget()
        self.phrases.setMinimumHeight(_PHRASES_MIN_PX)
        self.phrases.setAccessibleName("Фразы")
        self.remove_phrase_button = QPushButton("Удалить фразу")
        self.remove_phrase_button.clicked.connect(self._remove_phrase)
        card, inside = _card("Фразы")
        inside.addLayout(_row(self.phrase, self.like_button, self.never_button))
        inside.addWidget(_caption(PHRASE_HINT))
        inside.addWidget(self.phrases)
        inside.addWidget(self.remove_phrase_button, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _build_settings(self) -> QGroupBox:
        self.brands = QLineEdit(placeholderText="через запятую, например: Salomon, New Balance")
        self.brands.setAccessibleName("Бренды")
        self.brands.returnPressed.connect(self._save_brands)
        self.save_brands = QPushButton("Сохранить")
        self.save_brands.clicked.connect(self._save_brands)
        self.strength = QSpinBox(minimum=0, maximum=profile.STRENGTH_MAX)
        self.strength.setValue(profile.STRENGTH_DEFAULT)
        self.strength.setAccessibleName(STRENGTH)
        # QSS horizontal padding does not reach a spin box's text: inset it as budget_box does.
        self.strength.lineEdit().setTextMargins(SPACE["sm"], 0, SPACE["sm"], 0)
        self.strength.valueChanged.connect(self._save_strength)
        form = form_rows(
            ("Бренды", _row(self.brands, self.save_brands)),
            ("", _caption("По ним ищутся вещи на Kaspi, в этом порядке (до шести).")),
            (STRENGTH, _row(self.strength, _caption(_STRENGTH_HINT))),
        )
        card, inside = _card("Настройка")
        inside.addLayout(form)
        return card

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        self.no_extra.setVisible(not style.available())
        self._set_busy(False)
        self._show_model()
        self.reload()

    def reload(self) -> None:
        job = self._runner.submit(profile.load())
        job.finished.connect(self._fill)
        job.failed.connect(self._failed)

    def _fill(self, current: profile.Profile) -> None:
        if not self.brands.isModified():  # keep what the user is typing
            self.brands.setText(", ".join(current.brands))
        self.strength.blockSignals(True)  # showing the stored value is not a change
        self.strength.setValue(current.strength)
        self.strength.blockSignals(False)
        self._fill_phrases(current)
        self._fill_photos(current.photos)
        self.loaded = True

    def _fill_phrases(self, current: profile.Profile) -> None:
        self.phrases.clear()
        for title, phrases in ((LIKES, current.likes), (NEVERS, current.nevers)):
            for phrase in phrases:
                item = QListWidgetItem(f"{title}: {phrase_line(phrase)}")
                item.setData(_KEY, phrase.text)
                self.phrases.addItem(item)

    def _fill_photos(self, photos: list[profile.Photo]) -> None:
        self.photos.clear()
        for photo in photos:
            item = QListWidgetItem(_thumbnail(photo.path), NEVERS if photo.disliked else "")
            item.setData(_KEY, photo.name)
            item.setToolTip(photo.label)
            self.photos.addItem(item)
        self.photo_summary.setText(f"{PHOTOS}: {photo_count(photos)}. {_PRIVATE}")

    def _save_brands(self) -> None:
        self.brands.setModified(False)
        self._run(_brands(self.brands.text()))

    def _save_strength(self, value: int) -> None:
        self._run(_strength(value))

    def _add_phrase(self, never: bool) -> None:
        if text := self.phrase.text().strip():
            self._run(_phrase(text, never), clear=self.phrase)

    def _remove_phrase(self) -> None:
        if (item := self.phrases.currentItem()) is not None:
            self._run(_remove([item.data(_KEY)]))

    def _add_photos(self, disliked: bool) -> None:
        if paths := self._pick_photos(self):
            self._start_adding([Path(p) for p in paths], disliked)

    def _add_folder(self, disliked: bool) -> None:
        if folder := self._pick_folder(self):
            self._start_adding([Path(folder)], disliked)

    def _start_adding(self, paths: list[Path], disliked: bool) -> None:
        self._say(ADDING)
        self._set_busy(True)
        self.progress.setRange(0, 0)  # busy until the first batch says how many there are
        self._show_progress(True)
        self._adding = self._runner.submit(_photos(paths, disliked, self._photo_progress.emit))
        self._adding.finished.connect(self._adding_done)
        self._adding.cancelled.connect(lambda: self._adding_done(STOPPED))
        self._adding.failed.connect(self._failed)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.progress_count.setText(f"{done} из {total}")

    def _cancel_adding(self) -> None:
        if self._adding is not None:
            self.cancel_button.setEnabled(False)  # the batch under way finishes first
            self._adding.cancel()

    def _adding_done(self, line: str) -> None:
        self._adding = None
        self._show_progress(False)
        self._done(line, None)

    def _show_progress(self, shown: bool) -> None:
        self.progress.setVisible(shown)
        self.progress_count.setVisible(shown)
        self.progress_count.clear()
        self.cancel_button.setVisible(shown)
        self.cancel_button.setEnabled(shown)

    def _download(self) -> None:
        self.model_status.setText(DOWNLOADING)
        self._set_busy(True)
        job = self._runner.submit(_download())
        job.finished.connect(self._downloaded)
        job.failed.connect(self._failed)

    def _downloaded(self, line: str) -> None:
        self._set_busy(False)
        self._show_model()
        if line != MODEL_READY:
            self._say(line)

    def _show_model(self) -> None:
        """The model's state; the button only when there is something to download."""
        line = model_line()
        self.model_status.setText("" if line == NO_EXTRA else line)
        self.model_status.setVisible(line != NO_EXTRA)
        self.download_button.setVisible(line not in (NO_EXTRA, MODEL_READY))

    def _remove_photos(self) -> None:
        if keys := [item.data(_KEY) for item in self.photos.selectedItems()]:
            self._run(_remove(keys))

    def _run(self, work: Coroutine[Any, Any, str], clear: QLineEdit | None = None) -> None:
        """Store a change; show its line, empty ``clear`` once it is saved, read back."""
        job = self._runner.submit(work)
        job.finished.connect(lambda line: self._done(line, clear))
        job.failed.connect(self._failed)

    def _done(self, line: str, clear: QLineEdit | None) -> None:
        self._set_busy(False)
        self._say(line)
        self._show_model()  # adding photos downloads it the first time
        if clear is not None and line == SAVED:
            clear.clear()
        self.reload()

    def _failed(self, _message: str) -> None:
        self._adding = None
        self._show_progress(False)
        self._set_busy(False)
        self._say(STORE_DOWN)  # the details are in the log

    def _say(self, line: str) -> None:
        self.message.setText(line)
        self.message.setVisible(bool(line))

    def _set_busy(self, busy: bool) -> None:
        enabled = style.available() and not busy
        for button in (
            self.add_button,
            self.add_folder_button,
            self.add_no_button,
            self.add_no_folder_button,
            self.download_button,
        ):
            button.setEnabled(enabled)
