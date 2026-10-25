"""``buysmarter prefs``: the saved search settings, as the app's «Настройки поиска» screen.

The same keys, checks and messages as the screen (it takes its texts from here), and the same
stored types, so either entry point reads what the other wrote.
"""

import asyncio
import logging
from enum import StrEnum
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from buysmarter.i18n import kzt, source_label
from buysmarter.memory import prefs
from buysmarter.memory.prefs import (
    BUDGET,
    CITY,
    EXCLUDED_BRANDS,
    MARKETPLACES,
    as_budget,
    as_list,
    as_sources,
)
from buysmarter.services.collect import ALL_SOURCES

LABELS = {
    CITY: "Город по умолчанию",
    BUDGET: "Бюджет",
    MARKETPLACES: "Маркетплейсы",
    EXCLUDED_BRANDS: "Исключить бренды",
}
SAVED = "Сохранено. Новые значения применятся к следующему поиску."
STORE_DOWN = "Не удалось сохранить: хранилище недоступно. Подробности в логе."
NO_SOURCE = "Выберите хотя бы один маркетплейс."
_CLEARED: dict[str, Any] = {CITY: "", BUDGET: 0, MARKETPLACES: [], EXCLUDED_BRANDS: []}

Key = StrEnum("Key", {key: key for key in LABELS})  # type: ignore[misc]

prefs_app = typer.Typer(
    help="Saved search settings, as the app's «Настройки поиска»: show, set, clear.",
    invoke_without_command=True,
)
console = Console()
logger = logging.getLogger(__name__)


@prefs_app.callback()
def show(ctx: typer.Context) -> None:
    """Show the saved search settings."""
    if ctx.invoked_subcommand is not None:
        return
    stored = asyncio.run(prefs.get_all())
    table = Table(show_header=False)
    for key, label in LABELS.items():
        table.add_row(label, escape(_shown(key, stored.get(key))), f"[dim]{key}[/dim]")
    console.print(table)


_KeyArg = Annotated[Key, typer.Argument(help="city, budget, marketplaces or excluded_brands.")]


@prefs_app.command("set")
def set_(
    key: _KeyArg,
    value: Annotated[
        str, typer.Argument(help='e.g. "Астана", 40000, kaspi,olx, "Noname, Generic"')
    ],
) -> None:
    """Save one search setting."""
    _save(key.value, _parsed(key.value, value))


@prefs_app.command("clear")
def clear(key: _KeyArg) -> None:
    """Put one search setting back to its default."""
    _save(key.value, _CLEARED[key.value])


def _shown(key: str, value: Any) -> str:
    if key == BUDGET:
        budget = as_budget(value)
        return kzt(budget) if budget else "без лимита"
    if key == MARKETPLACES:
        names = as_sources(value)
        return ", ".join(map(source_label, names)) if names else "все"
    if key == EXCLUDED_BRANDS:
        return ", ".join(as_list(value)) or "—"
    return str(value or "") or "из запроса, иначе Алматы"


def _parsed(key: str, raw: str) -> Any:
    """The value the app's form would store for ``raw``; ``BadParameter`` says why not."""
    if key == BUDGET:
        digits = "".join(raw.split())
        if not digits.isdigit():
            raise typer.BadParameter("бюджет — число в тенге, например 40000")
        return as_budget(digits)
    if key == MARKETPLACES:
        names = [name.lower() for name in as_list(raw)]
        if unknown := [name for name in names if name not in ALL_SOURCES]:
            known = ", ".join(ALL_SOURCES)
            raise typer.BadParameter(f"неизвестный маркетплейс {', '.join(unknown)}; есть {known}")
        if not names:
            raise typer.BadParameter(NO_SOURCE)
        return as_sources(names)
    if key == EXCLUDED_BRANDS:
        return as_list(raw)
    return raw.strip()


def _save(key: str, value: Any) -> None:
    try:
        asyncio.run(prefs.set(key, value))
    except Exception as exc:  # noqa: BLE001 - say it plainly, the details go to the log
        logger.warning("Preference %s not saved: %r", key, exc)
        console.print(f"[red]{STORE_DOWN}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]{SAVED}[/green]")
