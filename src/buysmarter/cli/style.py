"""``buysmarter style``: the clothing-taste profile, as the app's «Стиль» screen.

The same texts as the screen (it takes them from here) and the same core calls
(``style.profile``), so either entry point sees what the other saved.
"""

import asyncio
import logging
from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import MofNCompleteColumn, Progress
from rich.table import Table
from rich.text import Text

from buysmarter import style
from buysmarter.cli.prefs import STORE_DOWN
from buysmarter.memory.prefs import as_list
from buysmarter.style import profile, weights
from buysmarter.style.profile import Phrase

if TYPE_CHECKING:
    from buysmarter.services.recommend import RecommendResult

BRANDS = "Бренды"
LIKES = "Нравится"
NEVERS = "Нет"
PHOTOS = "Фото"
STRENGTH = "Сила стиля"
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
CARD_UNKNOWN = (
    "Этого товара нет в сохранённых поисках: найдите его поиском, потом ставьте 👍/👎 по ссылке."
)
NO_CARD_PHOTO = "Не удалось скачать фото товара. Попробуйте позже."
LIKED = "Добавлено в образцы стиля: «{}»."
DISLIKED = "Добавлено в «нет»: «{}»."
# Under a console report where style works: the app shows 👍/👎 buttons instead.
VOTE_HINT = "👍/👎: buysmarter style like|dislike <ссылка>"
STOPPED = "Остановлено. Уже добавленные фото остались в образцах."
NOT_FOUND = "В профиле нет такого фото или фразы."
MODEL_DOWN = "Не удалось скачать модель стиля. Подробности в логе."
MODEL_READY = "Модель стиля скачана."
MODEL_MISSING = "Модель стиля не скачана (~300 МБ): скачается при первом добавлении фото."
MODEL = "Модель"
DOWNLOADING = "Скачиваю модель стиля (~300 МБ, один раз)…"
PHRASE_HINT = "Пишите, как выглядит вещь («белая глянцевая подошва»), а не «без …»."

style_app = typer.Typer(
    help="Clothing-taste profile, as the app's «Стиль»: brands, sample photos, phrases.",
    invoke_without_command=True,
)
console = Console()
logger = logging.getLogger(__name__)


def votes_work(result: "RecommendResult") -> bool:
    """👍/👎 can work for this report: style is on for the category, the extra is installed."""
    on = any(s.name == "style" and s.on for s in result.capabilities)
    return on and style.available()


def liked_line(sample: profile.Photo) -> str:
    return (DISLIKED if sample.disliked else LIKED).format(sample.label)


def strength_line(strength: int) -> str:
    if strength <= 0:
        return "0 — выключено"
    return f"{strength} (по умолчанию {profile.STRENGTH_DEFAULT}; 0 — выключено)"


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
    table.add_row(STRENGTH, strength_line(current.strength))
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


@style_app.command()
def strength(
    value: Annotated[
        int,
        typer.Argument(
            min=0, max=profile.STRENGTH_MAX, help="0 switches style off; 20 is the default."
        ),
    ],
) -> None:
    """Set how much style weighs in a clothing search (the «Стиль» axis, before normalizing)."""
    _run(profile.set_strength(value))
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
    try:
        added = _added_with_bar(files, disliked=no)
    except KeyboardInterrupt:
        _say(STOPPED, "yellow")
        raise typer.Exit(code=130) from None
    _say(added_line(added), "yellow" if added.skipped else "green")


def _added_with_bar(files: list[Path], disliked: bool) -> profile.Added:
    """``add_photos`` under a «N из M» bar; Ctrl+C stops after the batch under way."""
    with Progress(*Progress.get_default_columns(), MofNCompleteColumn(), transient=True) as bar:
        task = bar.add_task("Фото", total=None)
        return _run(
            profile.add_photos(
                files, disliked, lambda done, total: bar.update(task, completed=done, total=total)
            )
        )


@style_app.command()
def like(url: Annotated[str, typer.Argument(help="A product link from a report.")]) -> None:
    """Add a found product's main photo to the samples (the report's 👍)."""
    _add_linked(url, disliked=False)


@style_app.command()
def dislike(url: Annotated[str, typer.Argument(help="A product link from a report.")]) -> None:
    """Add a found product's main photo to «нет» (the report's 👎)."""
    _add_linked(url, disliked=True)


def _add_linked(url: str, disliked: bool) -> None:
    if not style.available():
        _say(NO_EXTRA, "red")
        raise typer.Exit(code=1)
    try:
        sample = _run(profile.add_linked(url, disliked=disliked))
    except (profile.NoCard, profile.NoCardPhoto) as exc:
        _say(CARD_UNKNOWN if isinstance(exc, profile.NoCard) else NO_CARD_PHOTO, "red")
        raise typer.Exit(code=1) from None
    _say(liked_line(sample), "green")


@style_app.command()
def download() -> None:
    """Download the style model now (~300 MB, once) instead of on the first photo."""
    if weights.missing():
        _say(DOWNLOADING, "dim")
    _run(profile.download_model())
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
    except (profile.Untranslated, profile.NoCard, profile.NoCardPhoto):
        raise
    except Exception as exc:  # noqa: BLE001 - say it plainly, the details go to the log
        logger.warning("Style profile not saved: %r", exc)
        down = MODEL_DOWN if isinstance(exc, weights.WeightsError) else STORE_DOWN
        _say(down, "red")
        raise typer.Exit(code=1) from exc


def _say(message: str, colour: str) -> None:
    """One message as written: no markup, no hard wraps (a test or a pipe reads it whole)."""
    console.print(Text(message, style=colour), soft_wrap=True)
