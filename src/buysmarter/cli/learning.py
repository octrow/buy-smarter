"""Learning a new category in the terminal: the same choices as the desktop app (#26, ADR-0006).

Before the search: learn first, or search now while the category learns in the background
(«Искать без изучения»). After a report without books: learn by hand (the report's «Изучить
категорию»). After either: «Повторить поиск?».
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape

from buysmarter.cli.progress import stages
from buysmarter.knowledge.generate import learn_category
from buysmarter.services.auto_learn import auto_learn, count_start
from buysmarter.services.recommend import RecommendResult
from buysmarter.services.recommend import recommend as run_recommend

logger = logging.getLogger(__name__)
console = Console()

LEARN_PROMPT = (
    "Категория «{category}» новая. Изучить её перед поиском "
    "(AI с поиском в интернете, 1–3 мин, платно)? Нет — искать сейчас, изучать в фоне"
)
LEARN_NOW_PROMPT = (
    "Для «{category}» нет данных о брендах. Изучить категорию сейчас "
    "(AI с поиском в интернете, 1–3 мин, платно)?"
)
RERUN_PROMPT = "Повторить поиск с новыми знаниями?"


async def learn_before_search(
    category: str, *, force: bool, background: dict[str, asyncio.Task[bool]] | None
) -> None:
    """Learn a new category before its search: with ``--learn``, or when the user agrees.

    A "no" searches now while the category learns in ``background`` («Искать без изучения»).
    A failed learn is said and the search goes on without the books.
    """
    if not force and not typer.confirm(LEARN_PROMPT.format(category=category), default=True):
        if background is not None:
            count_start()
            background[category] = asyncio.create_task(auto_learn(category))
            console.print("[dim]Ищу сейчас; категория изучается в фоне.[/dim]")
        return
    name = escape(repr(category))
    console.print(f"[dim]Изучаю категорию {name}…[/dim]")
    count_start()
    if await auto_learn(category):
        console.print(f"[green]Категория {name} изучена.[/green]")
    else:
        console.print(
            f"[yellow]Категорию {name} изучить не удалось (см. лог), ищу без неё.[/yellow]"
        )


async def search_and_follow_up(
    search: Callable[..., Awaitable[RecommendResult]],
    show: Callable[[RecommendResult], None],
    *,
    learn: bool,
    use_llm: bool,
    tty: bool,
) -> None:
    """Search, show the report, then offer what the desktop app offers after it (#26).

    One event loop for all of it: a learn started in the background lives on this loop.
    """
    background: dict[str, asyncio.Task[bool]] = {}
    settled: dict[str, Any] = {}
    result = await search(learn=learn, background=background, settled=settled)
    show(result)
    if await _learned_after_report(result, background, use_llm=use_llm, tty=tty):
        # the settled search again, as the app's «Повторить поиск»: nothing is asked twice
        with stages(console):
            rerun = await run_recommend(**{**settled, "learn_first": False})
        show(rerun)


async def _learned_after_report(
    result: RecommendResult,
    background: dict[str, asyncio.Task[bool]],
    *,
    use_llm: bool,
    tty: bool,
) -> bool:
    """True when the category got learned after its report and the user wants a new search.

    A background learn («Искать без изучения») is waited for; otherwise a report without books
    on a terminal offers to learn by hand, as the report's «Изучить категорию» button does.
    """
    if background:  # one search learns one category
        category, task = next(iter(background.items()))
        name = escape(repr(category))
        console.print(f"[dim]Дожидаюсь изучения категории {name}… (Ctrl-C — прервать)[/dim]")
        return _said(category, await task) and typer.confirm(RERUN_PROMPT, default=True)
    category = result.constraints.product_type or result.constraints.search_phrase
    if not (result.knowledge_missing and use_llm and not result.skipped_llm_steps):
        return False
    if not tty or not typer.confirm(LEARN_NOW_PROMPT.format(category=category), default=False):
        return False
    console.print(f"[dim]Изучаю категорию {escape(repr(category))}…[/dim]")
    try:
        await learn_category(category)
    except Exception:  # noqa: BLE001 - the report stands; say it, details in the log
        logger.exception("learning of %r failed", category)
        return _said(category, False)
    return _said(category, True) and typer.confirm(RERUN_PROMPT, default=True)


def _said(category: str, learned: bool) -> bool:
    name = escape(repr(category))
    if learned:
        console.print(f"[green]Категория {name} изучена.[/green]")
    else:
        console.print(f"[yellow]Категорию {name} изучить не удалось (см. лог).[/yellow]")
    return learned
