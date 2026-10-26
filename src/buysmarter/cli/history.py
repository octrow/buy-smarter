"""``history`` and ``refresh``: price history in the CLI, in the words of the app's «История цен».

The texts, the change and the drop lines are shared with ``gui/screens/history`` (it takes them
from here), so the two entry points say the same thing about the same snapshots.
"""

import asyncio

import typer
from rich.markup import escape
from rich.table import Table

from buysmarter.cli.console import console
from buysmarter.i18n import clean_title, kzt, source_label
from buysmarter.services.price_tracking import CategoryRefresh, PriceChange, ProductHistory
from buysmarter.services.price_tracking import product_history as run_product_history
from buysmarter.services.price_tracking import refresh_tracked as run_refresh_tracked

NOTHING_TRACKED = "Пока ничего не отслеживается — история появится после первого поиска."
NO_TRACKED_CATEGORIES = "Отслеживаемых категорий пока нет — сначала выполните поиск."
NO_HISTORY = "Нет истории для «{term}» (нет совпадений или хранилище недоступно)."
RECENT_TITLE = "Недавние товары"
RECENT_HEADERS = ("Товар", "Маркетплейс", "Цена", "Изменение")
SNAPSHOT_HEADERS = ("Дата", "Мин. цена", "Предложений")
DROP_HEADERS = ("Товар", "Было", "Стало", "Снижение")
_SPARKS = "▁▂▃▄▅▆▇█"


def price_change(entry: ProductHistory) -> str:
    """The latest move vs the snapshot before it: "▼ 3%", "▲ 5%", or "—"."""
    if len(entry.snapshots) < 2 or not entry.snapshots[1].min_price:
        return "—"
    new, old = entry.snapshots[0].min_price, entry.snapshots[1].min_price
    pct = round(100 * (new - old) / old)
    return "—" if pct == 0 else f"{'▲' if pct > 0 else '▼'} {abs(pct)}%"


def recent_cells(entry: ProductHistory) -> list[str]:
    price = kzt(entry.snapshots[0].min_price) if entry.snapshots else "—"
    return [clean_title(entry.title), source_label(entry.source), price, price_change(entry)]


def snapshot_rows(entry: ProductHistory) -> list[list[str]]:
    return [
        [s.captured_at.replace("T", " "), kzt(s.min_price), str(s.offer_count)]
        for s in entry.snapshots
    ]


def history_title(entry: ProductHistory) -> str:
    return f"{clean_title(entry.title)}  [{source_label(entry.source)}]"


def sparkline(prices: list[int]) -> str:
    """The min price over time in block characters, oldest first: the app's trend line."""
    if len(prices) < 2:
        return ""
    low, span = min(prices), (max(prices) - min(prices)) or 1
    return "".join(_SPARKS[int((p - low) / span * (len(_SPARKS) - 1))] for p in prices)


def category_line(result: CategoryRefresh) -> str:
    return f"{result.category}: товаров {result.product_count}, цена изменилась у {result.changed}"


def drop_cells(change: PriceChange) -> list[str]:
    return [
        clean_title(change.title),
        kzt(change.old_price),
        kzt(change.new_price),
        kzt(abs(change.delta)),
    ]


def refresh_summary(results: list[CategoryRefresh]) -> str:
    """One line per category, then its biggest price drops and by how much."""
    if not results:
        return NO_TRACKED_CATEGORIES
    lines = []
    for result in results:
        lines.append(category_line(result))
        for change in result.drops:
            title, old, new, cheaper = drop_cells(change)
            lines.append(f"   ▼ {title}: {old} → {new}, дешевле на {cheaper}")
    return "\n".join(lines)


def refresh() -> None:
    """Re-scan every tracked category and record fresh price snapshots."""
    results = asyncio.run(run_refresh_tracked())
    if not results:
        console.print(f"[yellow]{NO_TRACKED_CATEGORIES}[/yellow]")
        return
    for result in results:
        console.print(f"[bold]{escape(category_line(result))}[/bold]")
        if result.drops:
            table = Table(*DROP_HEADERS, header_style="bold")
            for change in result.drops:
                table.add_row(*map(escape, drop_cells(change)))
            console.print(table)


def history(
    query: str = typer.Argument(
        "", help="Product name or URL substring; without it, the recently tracked products."
    ),
) -> None:
    """Price history of a product, or the recently tracked products with their last change."""
    entries = asyncio.run(run_product_history(query))
    if not entries:
        text = NO_HISTORY.format(term=query) if query else NOTHING_TRACKED
        console.print(f"[yellow]{escape(text)}[/yellow]")
        return
    if not query:
        table = Table(*RECENT_HEADERS, title=RECENT_TITLE, header_style="bold")
        for entry in entries:
            table.add_row(*map(escape, recent_cells(entry)))
        console.print(table)
        return
    for entry in entries:
        console.print(f"[bold]{escape(history_title(entry))}[/bold]  {sparkline(_prices(entry))}")
        table = Table(*SNAPSHOT_HEADERS, header_style="bold")
        for cells in snapshot_rows(entry):
            table.add_row(*map(escape, cells))
        console.print(table)


def _prices(entry: ProductHistory) -> list[int]:
    return [s.min_price for s in reversed(entry.snapshots)]
