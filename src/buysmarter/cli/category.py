"""``buysmarter category``: the stored categories and their switches, as the app's «Категории».

``list`` and ``show`` are what the screen shows (``category_rows`` feeds both); ``set`` and
``reset`` are the screen's «Как оцениваем эту категорию» switches, written to the category's
own record with no parent (decision 6). Learning stays in ``learn-category`` and ``learn``.
"""

import asyncio
from enum import StrEnum
from typing import Annotated, Any, NamedTuple

import typer
from rich.markup import escape
from rich.table import Table

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.provenance import (
    CHANGED_NOTE,
    TOGGLES_TITLE,
    CapabilityState,
    capability_states,
    toggle_text,
)
from buysmarter.cli.console import console, render_knowledge
from buysmarter.cli.report import KnowledgeRows, knowledge_rows
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.knowledge.profile import (
    COMPOSITION_DRIVEN,
    SPEC_DRIVEN,
    CategoryProfile,
    profile_from_record,
)
from buysmarter.knowledge.store import (
    load_knowledge,
    resolve_profile,
    set_user_choice,
    stored_records,
)
from buysmarter.ranking.models import QueryConstraints

NOTHING_LEARNED = "Изученных категорий нет: buysmarter learn-category КАТЕГОРИЯ."
NOT_LEARNED = "Категория «{name}» ещё не изучена: buysmarter learn-category {name}"
HEADERS = ("Категория", "Книги", "Родитель", "Обновлено")

CategoryCapability = StrEnum(  # type: ignore[misc]
    "CategoryCapability", {n: n for n, cap in REGISTRY.items() if cap.level == "category"}
)
Switch = StrEnum("Switch", {"on": "on", "off": "off"})

category_app = typer.Typer(
    help="Learned categories, as the app's «Категории»: list, show, set, reset.",
    no_args_is_help=True,
)


class CategoryRow(NamedTuple):
    name: str
    books: str
    parent: str
    updated: str


def _books(profile: CategoryProfile) -> str:
    parts = []
    if profile.brand_book is not None:
        parts.append(f"бренды {len(profile.brand_book.tiers)}")
    if profile.model_book is not None:
        parts.append(f"модели {len(profile.model_book.entries)}")
    if profile.ingredient_book is not None:
        parts.append(f"вещества {len(profile.ingredient_book.substances)}")
    return ", ".join(parts) or "—"


def category_row(slug: str, raw: dict[str, Any]) -> CategoryRow:
    profile = profile_from_record(slug, raw)
    stamp = (raw.get("updated_by"), str(raw.get("updated_at") or "")[:10])  # the date only
    updated = ", ".join(filter(None, stamp))
    return CategoryRow(
        slug, _books(profile), f"из {profile.parent}" if profile.parent else "", updated
    )


async def category_rows() -> list[CategoryRow]:
    """Every stored category by name: its books, parent and who updated it when."""
    records = await stored_records()
    return [category_row(slug, records[slug]) for slug in sorted(records)]


@category_app.command("list")
def list_() -> None:
    """List the learned categories."""
    rows = asyncio.run(category_rows())
    if not rows:
        console.print(NOTHING_LEARNED)
        return
    table = Table(*HEADERS, header_style="bold")
    for row in rows:
        table.add_row(*map(escape, row))
    console.print(table)


class CategoryDetail(NamedTuple):
    row: CategoryRow
    knowledge: KnowledgeRows
    states: list[CapabilityState]  # the category-level switches


async def category_detail(name: str) -> CategoryDetail | None:
    """A stored category as a search would see it, no LLM; ``None`` when nothing is stored."""
    raw = await load_knowledge(name)
    if raw is None:
        return None
    profile = await resolve_profile(name, use_llm=False)
    ingredient = profile.ingredient_book
    knowledge = knowledge_rows(
        profile.brand_book or BrandBook(),
        profile.model_book or ModelBook(),
        COMPOSITION_DRIVEN if ingredient is not None else SPEC_DRIVEN,
        ingredient,
    )
    states = capability_states(REGISTRY, profile, QueryConstraints())
    return CategoryDetail(
        category_row(profile.slug, raw),
        knowledge,
        [state for state in states if state.level == "category"],
    )


@category_app.command("show")
def show(name: Annotated[str, typer.Argument(help='Category, e.g. "робот-пылесос".')]) -> None:
    """Show a category's knowledge and switches, without learning."""
    detail = asyncio.run(category_detail(name))
    if detail is None:
        console.print(NOT_LEARNED.format(name=escape(name)))
        raise typer.Exit(code=1)
    row = detail.row
    console.print(" · ".join(filter(None, (row.books, row.parent, row.updated))))
    render_knowledge(row.name, detail.knowledge)
    console.print(f"\n[bold]{TOGGLES_TITLE}[/bold]")
    for state in detail.states:
        console.print(f"  {escape(toggle_text(state))}  [dim]{state.name}[/dim]")


_NameArg = Annotated[str, typer.Argument(help="Category.")]
_CapabilityArg = Annotated[CategoryCapability, typer.Argument(help="Category capability.")]


@category_app.command("set")
def set_(
    name: _NameArg, capability: _CapabilityArg, switch: Annotated[Switch, typer.Argument()]
) -> None:
    """Switch a capability on or off for this category: your choice beats learning."""
    _write(name, capability.value, switch.value == "on")


@category_app.command("reset")
def reset(name: _NameArg, capability: _CapabilityArg) -> None:
    """Put a capability back to its automatic value."""
    _write(name, capability.value, None)


def _write(name: str, capability: str, choice: bool | None) -> None:
    asyncio.run(set_user_choice(name, capability, choice))
    console.print(f"[green]{CHANGED_NOTE}[/green]")
