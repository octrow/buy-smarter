"""``buysmarter style``: the clothing-taste profile, as the app's «Стиль» screen.

The same texts as the screen (it takes them from here) and the same core calls
(``style.profile``), so either entry point sees what the other saved.
"""

import asyncio
import logging
from collections.abc import Coroutine
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from buysmarter import style
from buysmarter.cli.prefs import STORE_DOWN
from buysmarter.memory.prefs import as_list
from buysmarter.style import profile, weights
from buysmarter.style.profile import Phrase

BRANDS = "Бренды"
LIKES = "Нравится"
NEVERS = "Нет"
PHOTOS = "Фото"
SAVED = "Сохранено. Профиль стиля применится к следующему поиску одежды."
NO_EXTRA = (
    "Подбор по фото выключен: в этой установке нет модуля стиля. "
    f"Установите его: pip install 'buysmarter[{style.EXTRA}]'."
)
UNTRANSLATED = (
    "Не удалось перевести фразу: модель стиля понимает только английский, а LLM недоступна. "
    "Напишите фразу по-английски, например «glossy white plastic sole»."
)
NOT_A_PHOTO = "не фото, пропущено"
NOT_FOUND = "В профиле нет такого фото или фразы."
MODEL_DOWN = "Не удалось скачать модель стиля. Подробности в логе."
MODEL_READY = "Модель стиля скачана."
MODEL_MISSING = "Модель стиля не скачана (~200 МБ): скачается при первом добавлении фото."
MODEL = "Модель"
DOWNLOADING = "Скачиваю модель стиля (~200 МБ, один раз)…"
PHRASE_HINT = "Пишите, как выглядит вещь («белая глянцевая подошва»), а не «без …»."

style_app = typer.Typer(
    help="Clothing-taste profile, as the app's «Стиль»: brands, sample photos, phrases.",
    invoke_without_command=True,
)
console = Console()
logger = logging.getLogger(__name__)


def photo_count(photos: list[profile.Photo]) -> str:
    disliked = sum(p.disliked for p in photos)
    return f"{len(photos) - disliked} «нравится», {disliked} «нет»"


def phrase_line(phrase: Phrase) -> str:
    """«как написано» → what the model reads, when they differ."""
    shown = "; ".join(phrase.en)
    return phrase.text if shown == phrase.text else f"{phrase.text} → {shown}"


def model_line() -> str:
    if not style.available():
        return NO_EXTRA
    return MODEL_MISSING if weights.missing() else MODEL_READY


def added_line(added: profile.Added) -> str:
    skipped = "".join(f"; {name}: {NOT_A_PHOTO}" for name in added.skipped)
    return f"Добавлено фото: {len(added.photos)}{skipped}."


@style_app.callback()
def show(ctx: typer.Context) -> None:
    """Show the style profile."""
    if ctx.invoked_subcommand is not None:
        return
    current = asyncio.run(profile.load())
    table = Table(show_header=False)
    table.add_row(BRANDS, escape(", ".join(current.brands) or "—"))
    table.add_row(LIKES, escape("\n".join(map(phrase_line, current.likes)) or "—"))
    table.add_row(NEVERS, escape("\n".join(map(phrase_line, current.nevers)) or "—"))
    table.add_row(MODEL, escape(model_line()))
    table.add_row(PHOTOS, f"{photo_count(current.photos)} [dim]({profile.samples_dir()})[/dim]")
    console.print(table)
    if current.photos:
        photos = Table("name", "file", "")
        for p in current.photos:
            photos.add_row(p.name, escape(p.label), NEVERS if p.disliked else LIKES)
        console.print(photos)


@style_app.command()
def brands(
    names: Annotated[str, typer.Argument(help='e.g. "Salomon, New Balance"; "" clears')],
) -> None:
    """Set the brands searched for clothing (in this order)."""
    _run(profile.set_brands(as_list(names)))
    _say(SAVED, "green")


@style_app.command("like-text")
def like_text(
    phrase: Annotated[str, typer.Argument(help="What you like, in any language.")],
) -> None:
    """Add a phrase for what you like."""
    _add_phrase(phrase, never=False)


@style_app.command()
def never(phrase: Annotated[str, typer.Argument(help="What you never want, as it looks.")]) -> None:
    """Add a phrase for what you never want; such items go down."""
    _add_phrase(phrase, never=True)


@style_app.command()
def add(
    files: Annotated[list[Path], typer.Argument(help="Photos or folders of clothing you like.")],
    no: Annotated[
        bool, typer.Option("--no", help="These are photos of what you never want.")
    ] = False,
) -> None:
    """Add sample photos (copied without EXIF; they never leave this computer)."""
    if not style.available():
        _say(NO_EXTRA, "red")
        raise typer.Exit(code=1)
    if weights.missing():
        _say(DOWNLOADING, "dim")
    added = _run(profile.add_photos(files, disliked=no))
    _say(added_line(added), "yellow" if added.skipped else "green")


@style_app.command()
def download() -> None:
    """Download the style model now (~200 MB, once) instead of on the first photo."""
    if weights.missing():
        _say(DOWNLOADING, "dim")
    _run(weights.download())
    _say(MODEL_READY, "green")


@style_app.command()
def remove(
    item: Annotated[str, typer.Argument(help="A photo name (see `style`) or a phrase.")],
) -> None:
    """Remove a sample photo or a phrase."""
    if not _run(profile.remove(item)):
        _say(NOT_FOUND, "red")
        raise typer.Exit(code=1)
    _say(SAVED, "green")


def _add_phrase(text: str, never: bool) -> None:
    try:
        phrase = _run(profile.add_phrase(text, never=never))
    except profile.Untranslated:
        _say(UNTRANSLATED, "red")
        raise typer.Exit(code=1) from None
    _say(f"{SAVED} {phrase_line(phrase)}", "green")
    if never:
        _say(PHRASE_HINT, "dim")


def _run[T](work: Coroutine[Any, Any, T]) -> T:
    try:
        return asyncio.run(work)
    except profile.Untranslated:
        raise
    except Exception as exc:  # noqa: BLE001 - say it plainly, the details go to the log
        logger.warning("Style profile not saved: %r", exc)
        down = MODEL_DOWN if isinstance(exc, weights.WeightsError) else STORE_DOWN
        _say(down, "red")
        raise typer.Exit(code=1) from exc


def _say(message: str, colour: str) -> None:
    """One message as written: no markup, no hard wraps (a test or a pipe reads it whole)."""
    console.print(Text(message, style=colour), soft_wrap=True)
