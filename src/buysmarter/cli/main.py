"""Typer CLI entry point for buy-smarter."""

import asyncio
import shutil
import sys
from pathlib import Path

import httpx
import typer
from market_sources.ozon.browser import OzonBrowser, ozon_state_path
from market_sources.ozon.source import search_path
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from buysmarter.cli.report import format_rejected_reasons, save_report
from buysmarter.config import get_settings
from buysmarter.db.base import db_reachable as _db_reachable
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.generate import (
    EXTRACT_KNOWLEDGE_MODEL,
    GENERATE_KNOWLEDGE_MODEL,
    extract_category_knowledge,
    generate_category_knowledge,
)
from buysmarter.knowledge.ingredients import IngredientBook, merge_ingredient_books
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.knowledge.store import (
    SPEC_DRIVEN,
    get_stored_books,
    merge_brand_books,
    merge_model_books,
    resolve_composition,
    save_knowledge,
)
from buysmarter.llm.review_summary import ReviewSummary
from buysmarter.logsetup import new_run_id, setup_logging
from buysmarter.ranking.models import QueryConstraints, ScoredCandidate
from buysmarter.ranking.selection import is_barred
from buysmarter.services.collect import ALL_SOURCES, select_sources
from buysmarter.services.price_tracking import CategoryRefresh, ProductHistory
from buysmarter.services.price_tracking import product_history as run_product_history
from buysmarter.services.price_tracking import refresh_tracked as run_refresh_tracked
from buysmarter.services.recommend import NoSubstanceMatch, RecommendResult, UsedListing
from buysmarter.services.recommend import parse_request as run_parse_request
from buysmarter.services.recommend import recommend as run_recommend

_DEFAULT_TOP = 8

app = typer.Typer(help="buy-smarter: AI-powered shopping assistant.")
console = Console()


@app.callback()
def _main() -> None:
    """buy-smarter: AI-powered shopping assistant (keeps subcommands named)."""
    setup_logging()
    new_run_id()


_QUERY_HELP = 'Natural-language request, e.g. "нужен ирригатор, IPX7, до 40000 тг, Алматы"'
_ROLE_LABELS = {
    "best_value": "Best value",
    "best_budget": "Best budget",
    "best_premium": "Best premium",
}


def _render_constraints(c: QueryConstraints, city_defaulted: bool = False) -> None:
    city_note = " (по умолчанию)" if city_defaulted else ""
    lines = [
        f"[bold]Category:[/bold] {c.category}",
        f"[bold]City:[/bold] {c.city or '-'}{city_note}",
        f"[bold]Budget:[/bold] {f'{c.budget_max:,} ₸' if c.budget_max else 'без лимита'}",
    ]
    if c.required_features:
        lines.append(f"[bold]Required:[/bold] {', '.join(c.required_features)}")
    if c.excluded_brands:
        lines.append(f"[bold]Excluded:[/bold] {', '.join(c.excluded_brands)}")
    console.print(Panel("\n".join(lines), title="Parsed request", expand=False))


def _render_best(best: ScoredCandidate) -> None:
    cand = best.candidate
    # M4: `best` can be a barred (suspicious-price/dup-gap/mimicry/
    # counterfeit-verdict) candidate ONLY via assemble_recommendation's
    # all-barred fallback — nothing trustworthy was left to prefer. That must
    # be visible, not silently presented as an endorsed pick.
    barred = is_barred(best)
    title = f"⚠ {cand.title}" if barred else cand.title
    header = (
        f"🥇 [bold]{escape(title)}[/bold]  [dim]\\[{escape(cand.source)}][/dim]\n"
        f"Score: [bold]{best.total_score:.0f}[/bold]/100   "
        f"Confidence: [bold]{best.confidence:.0%}[/bold]\n"
        f"Price: [bold]{cand.price:,} ₸[/bold]"
    )
    if barred:
        header += "\n[bold red]все кандидаты под подозрением — проверьте вручную[/bold red]"
    if best.review_penalty > 0:
        header += f"\n[red]Penalty: отзывы (−{best.review_penalty:.0f})[/red]"
    if cand.best_seller:
        header += f"\nПродавец: {escape(cand.best_seller)}"
    if cand.url:
        header += f"\n{escape(cand.url)}"
    console.print(Panel(header, title="Best pick", expand=False))

    table = Table(show_header=True, header_style="bold")
    table.add_column("Axis")
    table.add_column("Score", justify="right")
    table.add_column("Why")
    for axis in best.axes:
        table.add_row(
            escape(axis.name), f"{axis.score:.1f}/{axis.max_score:.0f}", escape(axis.explanation)
        )
    console.print(table)

    if best.pros:
        console.print("[green]+ " + escape("  + ".join(best.pros)) + "[/green]")
    if best.cons:
        console.print("[red]- " + escape("  - ".join(best.cons)) + "[/red]")


def _render_review_summary(summary: ReviewSummary | None) -> None:
    """Render the AI review summary panel for the best pick (docs/INIT_DATA.md)."""
    if summary is None:
        return
    lines = [f"[bold red]⚠ {escape(f)}[/bold red]" for f in summary.red_flags]
    lines += [f"[green]✓[/green] {escape(p)}" for p in summary.pros]
    lines += [f"[red]✗[/red] {escape(c)}" for c in summary.cons]
    lines += [f"[yellow]«{escape(c)}»[/yellow]" for c in summary.common_complaints]
    if summary.verdict_line:
        lines.append(f"\n[bold]{escape(summary.verdict_line)}[/bold]")
    if not lines:
        return
    console.print(Panel("\n".join(lines), title="Отзывы кратко", expand=False))


def _product_cell(title: str, url: str | None) -> str:
    """Product name, with the URL on a second dim line when present."""
    return f"{escape(title)}\n[dim]{escape(url)}[/dim]" if url else escape(title)


def _render_alternatives(alts: dict[str, ScoredCandidate]) -> None:
    seen: set[str] = set()
    table = Table(title="Alternatives", show_header=True, header_style="bold")
    table.add_column("Role")
    table.add_column("Source")
    table.add_column("Product")
    table.add_column("Price", justify="right")
    table.add_column("Score", justify="right")
    for role, scored in alts.items():
        key = scored.candidate.source_product_id
        if key in seen:
            continue
        seen.add(key)
        table.add_row(
            _ROLE_LABELS.get(role, role),
            scored.candidate.source,
            _product_cell(scored.candidate.title, scored.candidate.url),
            f"{scored.candidate.price:,} ₸",
            f"{scored.total_score:.0f}",
        )
    console.print(table)


def _tier_cell(c) -> str:
    """Tier column: a mimic candidate flags the counterfeit signal instead of
    the (usually empty) tier — "-" alone would hide the more useful fact."""
    if c.mimic_brand is not None:
        return f"подделка под {escape(c.mimic_brand)}?"
    return escape(c.brand_tier or "-")


def _render_top(scored: list[ScoredCandidate], top: int) -> None:
    ranked = scored[:top]
    if not ranked:
        return
    title = f"Top candidates (top {len(ranked)})"
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Source")
    table.add_column("Product")
    table.add_column("Price", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Tier")
    for i, s in enumerate(ranked, start=1):
        c = s.candidate
        # A barred candidate (suspicious price, dup-price-gap, mimicry, or a
        # review-summary counterfeit verdict) stays visible in this table but
        # gets a visible "⚠" marker — it can never win best/alternative roles
        # (docs/PLAN.md "Ranking-фиксы"), and the table shouldn't hide that.
        title_text = f"⚠ {c.title}" if is_barred(s) else c.title
        table.add_row(
            str(i),
            c.source,
            _product_cell(title_text, c.url),
            f"{c.price:,} ₸",
            f"{s.total_score:.0f}",
            _tier_cell(c),
        )
    console.print(table)


def _render_used(used: list[UsedListing]) -> None:
    if not used:
        return
    table = Table(title="Б/у на OLX", show_header=True, header_style="bold")
    table.add_column("Listing")
    table.add_column("Price", justify="right")
    table.add_column("Saving vs new", justify="right")
    table.add_column("City")
    table.add_column("Con")
    for listing in used:
        title = f"{listing.title} → {listing.canonical}" if listing.canonical else listing.title
        saving_cell = "—" if listing.standalone else f"-{listing.saving:,} ₸"
        table.add_row(
            _product_cell(title, listing.url),
            f"{listing.price:,} ₸",
            saving_cell,
            escape(listing.city or "-"),
            escape(listing.con),
        )
    console.print(table)


def _render(result: RecommendResult, top: int) -> None:
    _render_constraints(result.constraints, result.city_defaulted)
    _render_best(result.recommendation.best)
    _render_review_summary(result.review_summary)
    _render_alternatives(result.recommendation.alternatives)
    _render_top(result.recommendation.ranked, top)
    _render_used(result.used_listings)
    if result.recommendation.rejected_count:
        console.print(
            f"[dim]{result.recommendation.rejected_count} candidate(s) "
            "rejected by hard filters.[/dim]"
        )
        if result.recommendation.rejected_reasons:
            breakdown = format_rejected_reasons(result.recommendation.rejected_reasons)
            console.print(f"[dim]Отсеяно: {breakdown}[/dim]")


def _render_no_substance(exc: NoSubstanceMatch) -> None:
    """Honest empty-pool output: the message + rejected-by-substance alternatives.

    Deliberately no best-pick panel — a product lacking the requested active
    substance must never be presented as a recommendation.
    """
    console.print(f"[yellow]{escape(exc.message())}[/yellow]")
    if not exc.alternatives:
        return
    table = Table(
        title="Ближайшие альтернативы (без требуемого вещества)",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Product")
    table.add_column("Price", justify="right")
    table.add_column("Rating", justify="right")
    for cand in exc.alternatives:
        rating = f"{cand.rating:.1f}" if cand.rating is not None else "-"
        table.add_row(_product_cell(cand.title, cand.url), f"{cand.price:,} ₸", rating)
    console.print(table)


@app.command()
def recommend(
    query: str = typer.Argument(..., help=_QUERY_HELP),
    city: str | None = typer.Option(None, "--city", "-c", help="Override city."),
    budget: int | None = typer.Option(None, "--budget", "-b", help="Override max price (KZT)."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Force keyless naive query parsing."),
    trusted_only: bool = typer.Option(
        False, "--trusted-only", help="Reject products from unknown/untrusted brands."
    ),
    sources: str = typer.Option(
        ",".join(ALL_SOURCES),
        "--sources",
        help="Comma-separated marketplaces to query, e.g. 'kaspi,ozon,olx' or 'kaspi'.",
    ),
    top: int = typer.Option(
        _DEFAULT_TOP, "--top", min=1, help="How many ranked candidates to show in the table."
    ),
    no_save: bool = typer.Option(
        False,
        "--no-save",
        help="Skip the markdown report (saved to ~/.local/share/buysmarter/reports/ by default).",
    ),
    save_path: str | None = typer.Option(
        None, "--save-path", help="Explicit report path (implies saving even with --no-save)."
    ),
    no_review_summary: bool = typer.Option(
        False, "--no-review-summary", help="Skip the AI review summary of the best pick."
    ),
) -> None:
    """Recommend the best product for a natural-language request."""
    selected = _parse_sources(sources)
    try:
        result = asyncio.run(
            recommend_pipeline(
                query,
                city,
                budget,
                not no_llm,
                trusted_only,
                selected,
                with_review_summary=not no_review_summary,
            )
        )
    except NoSubstanceMatch as exc:
        _render_no_substance(exc)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(f"[yellow]No recommendation: {escape(str(exc))}[/yellow]")
        raise typer.Exit(code=1) from exc
    except httpx.HTTPError as exc:
        console.print(f"[red]Marketplace request failed: {escape(repr(exc))}[/red]")
        raise typer.Exit(code=2) from exc
    _render(result, top)
    if save_path or not no_save:
        path = save_report(result, query, top, save_path)
        console.print(f"[green]Report saved to {escape(str(path))}[/green]")


def _parse_sources(raw: str) -> list[str]:
    """Parse the ``--sources`` CSV into a validated ordered marketplace list."""
    try:
        return select_sources(raw.split(","))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


_BUDGET_PROMPT = "Бюджет в ₸ (Enter — без лимита)"


def _resolve_budget(budget: int | None, parsed_budget: int | None) -> int | None:
    """Fill in a missing budget interactively, TTY only.

    ``--budget`` wins outright; a budget already found in the query is left
    alone (``recommend`` keeps it, no override needed); otherwise ask — but
    only on a real terminal, so scripts/CI never block on stdin.
    """
    if budget is not None or parsed_budget is not None:
        return budget
    if not sys.stdin.isatty():
        return None
    answer = typer.prompt(_BUDGET_PROMPT, default="", show_default=False).strip()
    digits = answer.replace(" ", "")
    return int(digits) if digits.isdigit() else None


async def recommend_pipeline(
    query: str,
    city: str | None,
    budget: int | None,
    use_llm: bool,
    trusted_only: bool = False,
    sources: list[str] | None = None,
    with_review_summary: bool = True,
) -> RecommendResult:
    """Parse once, prompt for a missing budget, then run the recommend pipeline."""
    parsed = await run_parse_request(query, use_llm)
    budget = _resolve_budget(budget, parsed.budget_max)
    return await run_recommend(
        query,
        city=city,
        budget=budget,
        use_llm=use_llm,
        trusted_only=trusted_only,
        sources=sources,
        parsed=parsed,
        with_review_summary=with_review_summary,
    )


def _render_knowledge_summary(
    category: str,
    brand: BrandBook,
    model: ModelBook,
    category_type: str = "spec_driven",
    ingredient: IngredientBook | None = None,
) -> None:
    """Print a tiers-and-models summary table for a category's knowledge."""
    tiers: dict[str, list[str]] = {"A": [], "B": [], "C": []}
    for name, tier in brand.tiers.items():
        tiers.setdefault(tier, []).append(name)
    table = Table(title=f"Brand tiers — {category}", show_header=True, header_style="bold")
    table.add_column("Tier")
    table.add_column("N", justify="right")
    table.add_column("Brands")
    for tier in ("A", "B", "C"):
        brands = tiers.get(tier, [])
        table.add_row(tier, str(len(brands)), escape(", ".join(brands)) or "-")
    console.print(table)
    console.print(
        f"[bold]Type:[/bold] {category_type}   "
        f"[bold]Models:[/bold] {len(model.entries)}   "
        f"[bold]Brands:[/bold] {len(brand.tiers)}   "
        f"[bold]Aliases:[/bold] {len(brand.aliases)}"
    )
    _render_ingredient_summary(ingredient)


def _render_ingredient_summary(ingredient: IngredientBook | None) -> None:
    """Print the active-substance table for composition-driven categories."""
    if ingredient is None or not ingredient.substances:
        return
    table = Table(title="Active substances", show_header=True, header_style="bold")
    table.add_column("Substance")
    table.add_column("Concentrations")
    table.add_column("Equivalents")
    for sub in ingredient.substances:
        equivalents = ", ".join(e.name for e in sub.equivalents) or "-"
        table.add_row(sub.canonical, ", ".join(sub.typical_concentrations) or "-", equivalents)
    console.print(table)


@app.command("learn-category")
def learn_category(
    category: str = typer.Argument(..., help='Product category, e.g. "робот-пылесос".'),
    web: bool = typer.Option(
        True,
        "--web/--no-web",
        help="Ground the generation in web search (claude CLI WebSearch).",
    ),
) -> None:
    """Generate a BrandBook + ModelBook for a category via the LLM and save it."""
    try:
        brand, model, category_type, ingredient = asyncio.run(
            _run_learn_category(category, web_search=web)
        )
    except Exception as exc:  # noqa: BLE001 - surface a clean CLI error
        console.print(f"[red]Knowledge generation failed: {escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    generated_by = f"{GENERATE_KNOWLEDGE_MODEL} + web search" if web else GENERATE_KNOWLEDGE_MODEL
    console.print(
        f"[green]Saved knowledge for {escape(repr(category))} "
        f"(generated by {generated_by}).[/green]"
    )
    _render_knowledge_summary(category, brand, model, category_type, ingredient)


async def _run_learn_category(
    category: str, *, web_search: bool = True
) -> tuple[BrandBook, ModelBook, str, IngredientBook]:
    brand, model, category_type, ingredient = await generate_category_knowledge(
        category, web_search=web_search
    )
    await save_knowledge(
        category,
        brand,
        model,
        GENERATE_KNOWLEDGE_MODEL,
        category_type=category_type,
        ingredient_book=ingredient,
    )
    return brand, model, category_type, ingredient


@app.command("learn")
def learn(
    file: str = typer.Argument(..., help="Markdown research notes file."),
    category: str = typer.Option(..., "--category", help="Category the notes describe."),
) -> None:
    """Extract books from your own notes and MERGE them into the stored knowledge."""
    try:
        brand, model, category_type, ingredient = asyncio.run(_run_learn_file(Path(file), category))
    except Exception as exc:  # noqa: BLE001 - surface a clean CLI error
        console.print(f"[red]Learning from file failed: {escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(
        f"[green]Merged notes from {escape(file)} into knowledge "
        f"for {escape(repr(category))}.[/green]"
    )
    _render_knowledge_summary(category, brand, model, category_type, ingredient)


async def _run_learn_file(
    file: Path, category: str
) -> tuple[BrandBook, ModelBook, str, IngredientBook]:
    notes = file.read_text(encoding="utf-8")
    new_brand, new_model, new_type, new_ingredient = await extract_category_knowledge(
        category, notes
    )
    stored_brand, stored_model = await get_stored_books(category)
    stored_type, stored_ingredient = await resolve_composition(category, use_llm=False)
    # User's file wins on conflicts → it is the overlay on top of the stored base.
    merged_brand = merge_brand_books(stored_brand or BrandBook(), new_brand)
    merged_model = merge_model_books(stored_model or ModelBook(), new_model)
    merged_ingredient = merge_ingredient_books(
        stored_ingredient or IngredientBook(), new_ingredient
    )
    # An extraction that found no substances must not downgrade a stored
    # composition-driven category back to spec-driven.
    category_type = new_type if new_type != SPEC_DRIVEN else stored_type
    await save_knowledge(
        category,
        merged_brand,
        merged_model,
        EXTRACT_KNOWLEDGE_MODEL,
        category_type=category_type,
        ingredient_book=merged_ingredient,
    )
    return merged_brand, merged_model, category_type, merged_ingredient


def _render_refresh(result: CategoryRefresh) -> None:
    """One compact line per category + a small table of the biggest drops."""
    console.print(
        f"[bold]{escape(result.category)}[/bold]: {result.product_count} products, "
        f"{result.changed} price change(s)"
    )
    if not result.drops:
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Product")
    table.add_column("Was", justify="right")
    table.add_column("Now", justify="right")
    table.add_column("Drop", justify="right")
    for change in result.drops:
        table.add_row(
            escape(change.title[:50]),
            f"{change.old_price:,} ₸",
            f"{change.new_price:,} ₸",
            f"[green]{change.delta:,} ₸[/green]",
        )
    console.print(table)


@app.command()
def refresh() -> None:
    """Re-scan every tracked category and record fresh price snapshots."""
    results = asyncio.run(run_refresh_tracked())
    if not results:
        console.print(
            "[yellow]No tracked categories yet — run `recommend` first "
            "(or Postgres is unavailable).[/yellow]"
        )
        return
    for result in results:
        _render_refresh(result)


def _render_history(entry: ProductHistory) -> None:
    """Snapshot table (date, min price, offers) for one matched product."""
    title = f"{escape(entry.title)}  [dim]\\[{escape(entry.source)}][/dim]"
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("Date")
    table.add_column("Min price", justify="right")
    table.add_column("Offers", justify="right")
    for snap in entry.snapshots:
        table.add_row(snap.captured_at, f"{snap.min_price:,} ₸", str(snap.offer_count))
    console.print(table)


@app.command()
def history(
    query: str = typer.Argument(..., help="Product name or URL substring to look up."),
) -> None:
    """Show the recorded price-snapshot history for a matching product."""
    entries = asyncio.run(run_product_history(query))
    if not entries:
        console.print(
            f"[yellow]No history for {escape(repr(query))} "
            "(no match, or Postgres unavailable).[/yellow]"
        )
        return
    for entry in entries:
        _render_history(entry)


_OZON_SETUP_PROBE_QUERY = "смартфон"


@app.command("ozon-setup")
def ozon_setup() -> None:
    """Open a visible browser, pass Ozon's anti-bot check once, and save the session."""
    path = ozon_state_path()
    browser = OzonBrowser(base_url=get_settings().ozon_base_url, state_path=path)
    probe = browser.composer_url(search_path(_OZON_SETUP_PROBE_QUERY))
    console.print("Откроется окно браузера. Дождитесь результатов поиска Ozon…")
    if not asyncio.run(browser.bootstrap(probe)):
        console.print("[red]Не удалось получить сессию Ozon за отведённое время.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Сессия Ozon сохранена: {escape(str(path))}[/green]")


def _chromium_installed() -> bool:
    import playwright
    from playwright.sync_api import sync_playwright

    # A headless launch, not a path check: the container ships only the headless shell.
    # The slim image has no Playwright driver at all, and launching without it logs a traceback.
    if not (Path(playwright.__file__).parent / "driver").is_dir():
        return False
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
            return True
    except Exception:
        return False


def doctor_checks() -> list[tuple[str, bool, str]]:
    """(name, ok, hint) for every optional piece the pipeline can use."""
    settings = get_settings()
    return [
        (
            "Postgres",
            asyncio.run(_db_reachable(settings.database_url)),
            "history and preferences off; in a clone run `make up && uv run alembic upgrade head`",
        ),
        (
            "claude CLI",
            shutil.which("claude") is not None,
            "LLM parsing and review summaries off; install Claude Code or use --no-llm",
        ),
        (
            "Playwright Chromium",
            _chromium_installed(),
            f'Ozon off; run `"{sys.executable}" -m playwright install chromium`',
        ),
        (
            "Ozon session",
            ozon_state_path().exists(),
            "Ozon may hit the anti-bot page; run `buysmarter ozon-setup`",
        ),
    ]


@app.command()
def doctor() -> None:
    """Check what is set up; every missing piece only disables part of the pipeline."""
    for name, ok, hint in doctor_checks():
        mark = "[green]ok[/green]" if ok else f"[yellow]missing[/yellow]  {escape(hint)}"
        console.print(f"{name:<20} {mark}")


if __name__ == "__main__":
    app()
