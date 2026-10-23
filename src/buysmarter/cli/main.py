"""Typer CLI entry point for buy-smarter."""

import asyncio
import logging
import sys
from pathlib import Path

import httpx
import typer
from market_sources.ozon.browser import ozon_state_path
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from buysmarter import local_store
from buysmarter.cli.clarify import CLARIFY_PROMPT as CLARIFY_PROMPT  # re-export
from buysmarter.cli.clarify import EXIT_CLARIFY, print_clarification
from buysmarter.cli.clarify import ask as ask_clarification
from buysmarter.cli.report import (
    DEFAULT_TOP,
    ROLE_LABELS,
    budget_text,
    format_rejected_reasons,
    no_new_pick_text,
    request_notes,
    save_report,
    skipped_llm_note,
    unavailable_sources_note,
)
from buysmarter.i18n import USED_SECTION, checked_brands, kzt, used_con
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.generate import (
    EXTRACT_KNOWLEDGE_MODEL,
    GENERATE_KNOWLEDGE_MODEL,
    extract_category_knowledge,
    learned_benchmark,
)
from buysmarter.knowledge.generate import learn_category as learn_category_knowledge
from buysmarter.knowledge.ingredients import IngredientBook, merge_ingredient_books
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.knowledge.profile import COMPOSITION_DRIVEN, CategoryProfile
from buysmarter.knowledge.store import (
    load_profile,
    merge_brand_books,
    merge_model_books,
    save_knowledge,
)
from buysmarter.llm import keys
from buysmarter.llm.adapter import LlmError
from buysmarter.llm.review_summary import ReviewSummary
from buysmarter.logsetup import new_run_id, setup_logging
from buysmarter.ranking.models import Candidate, QueryConstraints, ScoredCandidate
from buysmarter.ranking.selection import distinct_variants, is_barred
from buysmarter.services.auto_learn import auto_learn, count_start, should_auto_learn
from buysmarter.services.collect import ALL_SOURCES, select_sources
from buysmarter.services.doctor import (
    INSTALL_BROWSER,
    doctor_checks,
    llm_check,
    pass_ozon_check,
)
from buysmarter.services.matching import UsedListing
from buysmarter.services.ozon_browser import setup_browser
from buysmarter.services.price_tracking import CategoryRefresh, ProductHistory
from buysmarter.services.price_tracking import product_history as run_product_history
from buysmarter.services.price_tracking import refresh_tracked as run_refresh_tracked
from buysmarter.services.recommend import (
    Clarify,
    NoNewPick,
    NoSubstanceMatch,
    RecommendResult,
    understand_request,
)
from buysmarter.services.recommend import parse_request as run_parse_request
from buysmarter.services.recommend import recommend as run_recommend
from buysmarter.services.understanding import NeedsClarification, NothingToSearch

_DEFAULT_TOP = DEFAULT_TOP

app = typer.Typer(help="buy-smarter: AI-powered shopping assistant.")
logger = logging.getLogger(__name__)
console = Console()


@app.callback()
def _main() -> None:
    """buy-smarter: AI-powered shopping assistant (keeps subcommands named)."""
    setup_logging()
    new_run_id()
    local_store.prepare()


_QUERY_HELP = 'Natural-language request, e.g. "нужен ирригатор, IPX7, до 40000 тг, Алматы"'


def _render_constraints(c: QueryConstraints, city_defaulted: bool = False) -> None:
    city_note = " (по умолчанию)" if city_defaulted else ""
    lines = [
        f"[bold]Category:[/bold] {c.search_phrase}",
        f"[bold]City:[/bold] {c.city or '-'}{city_note}",
        f"[bold]Budget:[/bold] {budget_text(c)}",
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
        f"Price: [bold]{kzt(cand.price)}[/bold]"
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
            ROLE_LABELS.get(role, role),
            scored.candidate.source,
            _product_cell(scored.candidate.title, scored.candidate.url),
            f"{kzt(scored.candidate.price)}",
            f"{scored.total_score:.0f}",
        )
    console.print(table)


def _tier_cell(c: Candidate) -> str:
    """Tier column: a mimic candidate flags the counterfeit signal instead of
    the (usually empty) tier — "-" alone would hide the more useful fact."""
    if c.mimic_brand is not None:
        return f"подделка под {escape(c.mimic_brand)}?"
    return escape(c.brand_tier or "-")


def _render_top(scored: list[ScoredCandidate], top: int) -> None:
    ranked = distinct_variants(scored, top)
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
            f"{kzt(c.price)}",
            f"{s.total_score:.0f}",
            _tier_cell(c),
        )
    console.print(table)


def _render_used(used: list[UsedListing], ranked: list[ScoredCandidate]) -> None:
    if not used:
        return
    brands = checked_brands(s.candidate for s in ranked)
    table = Table(title=USED_SECTION, show_header=True, header_style="bold")
    table.add_column("Listing")
    table.add_column("Price", justify="right")
    table.add_column("Saving vs new", justify="right")
    table.add_column("City")
    table.add_column("Con")
    for listing in used:
        title = f"{listing.title} → {listing.canonical}" if listing.canonical else listing.title
        saving_cell = "—" if listing.standalone else f"-{kzt(listing.saving)}"
        table.add_row(
            _product_cell(title, listing.url),
            f"{kzt(listing.price)}",
            saving_cell,
            escape(listing.city or "-"),
            escape(used_con(listing, brands)),
        )
    console.print(table)


def _render(result: RecommendResult, top: int) -> None:
    if result.restricted_note:
        console.print(f"[bold yellow]⚠ {escape(result.restricted_note)}[/bold yellow]")
    _render_constraints(result.constraints, result.city_defaulted)
    notes = (*request_notes(result), skipped_llm_note(result), unavailable_sources_note(result))
    for note in notes:
        if note:
            console.print(f"[yellow]{escape(note)}[/yellow]")
    _render_best(result.recommendation.best)
    _render_review_summary(result.review_summary)
    _render_alternatives(result.recommendation.alternatives)
    _render_top(result.recommendation.ranked, top)
    _render_used(result.used_listings, result.recommendation.ranked)
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
        table.add_row(_product_cell(cand.title, cand.url), f"{kzt(cand.price)}", rating)
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
    no_clarify: bool = typer.Option(
        False, "--no-clarify", help="Never ask: search the first option of an ambiguous request."
    ),
    learn: bool = typer.Option(
        False, "--learn", help="Learn a new category after the report without asking."
    ),
) -> None:
    """Recommend the best product for a natural-language request.

    An ambiguous request is asked about first; off a terminal the question is printed and
    the exit code is 3.
    """
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
                no_clarify=no_clarify,
            )
        )
    except NeedsClarification as exc:
        print_clarification(console, exc.clarification)
        raise typer.Exit(code=EXIT_CLARIFY) from exc
    except NothingToSearch as exc:
        console.print(f"[yellow]{escape(str(exc))}[/yellow]")
        raise typer.Exit(code=1) from exc
    except NoSubstanceMatch as exc:
        _render_no_substance(exc)
        raise typer.Exit(code=1) from exc
    except NoNewPick as exc:
        text = no_new_pick_text(exc.rejected_reasons, bool(exc.used_listings))
        console.print(f"[yellow]{escape(text)}[/yellow]")
        _render_used(exc.used_listings, [])
        raise typer.Exit(code=0 if exc.used_listings else 1) from exc
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
    if asyncio.run(_offer_learning(result, use_llm=not no_llm, force=learn)):
        _rerun(query, city, budget, not no_llm, trusted_only, selected, top, no_clarify)


_LEARN_PROMPT = "Изучить категорию сейчас (1–3 мин)?"
_RERUN_PROMPT = "Повторить поиск с новыми знаниями?"


def _on_terminal() -> bool:
    return sys.stdin.isatty()


async def _offer_learning(result: RecommendResult, *, use_llm: bool, force: bool) -> bool:
    """After the report: learn a new category (asked on a terminal, or ``--learn``).

    Returns True when it was learned and the user, on a terminal, wants the search repeated.
    One event loop for the check and the learn: the store's connections belong to it.
    """
    c = result.constraints
    if not await should_auto_learn(
        c.product_type,
        intent=c.intent,
        use_llm=use_llm,
        knowledge_missing=result.knowledge_missing,
    ):
        return False
    tty = _on_terminal()
    if not force and not (tty and typer.confirm(_LEARN_PROMPT, default=False)):
        return False
    name = escape(repr(c.product_type))
    console.print(f"[dim]Изучаю категорию {name}…[/dim]")
    count_start()
    if not await auto_learn(c.product_type):
        console.print(f"[yellow]Категорию {name} изучить не удалось (см. лог).[/yellow]")
        return False
    console.print(f"[green]Категория {name} изучена.[/green]")
    return tty and typer.confirm(_RERUN_PROMPT, default=False)


def _rerun(
    query: str,
    city: str | None,
    budget: int | None,
    use_llm: bool,
    trusted_only: bool,
    sources: list[str],
    top: int,
    no_clarify: bool,
) -> None:
    """Search again with the new knowledge and show it (not saved: the report is already)."""
    # ponytail: a failed rerun only prints; it does not map to the exit codes of the first run.
    try:
        result = asyncio.run(
            recommend_pipeline(
                query, city, budget, use_llm, trusted_only, sources, no_clarify=no_clarify
            )
        )
    except Exception as exc:  # noqa: BLE001 - the first report stands; say why this one is missing
        logger.exception("repeat search failed")
        console.print(f"[red]Повторный поиск не удался: {escape(str(exc))}[/red]")
        return
    _render(result, top)


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
    no_clarify: bool = False,
) -> RecommendResult:
    """Parse once, settle any question, prompt for a missing budget, then recommend.

    On a terminal a clarification is asked here (an option goes back as ``answer``, the
    user's own text joins the query, which is parsed again) and the next round runs; the
    budget typed once holds for every round. Off a terminal ``NeedsClarification`` leaves.
    """
    parsed = await run_parse_request(query, use_llm)
    answer, clarify_round, budget_asked = None, 0, False
    while True:
        ask = Clarify(answer, clarify_round, no_clarify)
        try:
            if not budget_asked:
                _, understood, _ = await understand_request(query, parsed, use_llm, ask)
                budget, budget_asked = _resolve_budget(budget, understood.budget_max), True
            return await run_recommend(
                query,
                city=city,
                budget=budget,
                use_llm=use_llm,
                trusted_only=trusted_only,
                sources=sources,
                parsed=parsed,
                with_review_summary=with_review_summary,
                answer=answer,
                clarify_round=clarify_round,
                no_clarify=no_clarify,
            )
        except NeedsClarification as exc:
            if not sys.stdin.isatty():
                raise
            asked, clarify_round = exc.clarification, clarify_round + 1
            reply = ask_clarification(console, asked)
            if reply is None:
                no_clarify = True
            elif reply in asked.options and asked.kind != "capability":
                answer = reply
            else:
                query, answer = f"{query} {reply}", None
                parsed = await run_parse_request(query, use_llm)


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
        brand, model, category_type, ingredient, _ = asyncio.run(
            learn_category_knowledge(category, web_search=web)
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


# A long notes file can outgrow one answer: the JSON comes back cut off mid-way.
_LEARN_LLM_HINT = (
    "The LLM answer was cut off or malformed, most often because the notes are too long for one "
    "answer. Nothing was saved: split the notes into smaller files (about 15 models each) and run "
    "`learn` on each."
)


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
        if isinstance(exc, LlmError):
            console.print(_LEARN_LLM_HINT)
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
    new_brand, new_model, new_type, new_ingredient, provider = await extract_category_knowledge(
        category, notes
    )
    # The category's own record only: a parent's or a related category's knowledge
    # must not be copied into this one (phase 1, decision 6).
    stored = await load_profile(category) or CategoryProfile(slug=category)
    stored_brand, stored_model = stored.brand_book, stored.model_book
    stored_ingredient = stored.ingredient_book
    # User's file wins on conflicts → it is the overlay on top of the stored base.
    merged_brand = merge_brand_books(stored_brand or BrandBook(), new_brand)
    merged_model = merge_model_books(stored_model or ModelBook(), new_model)
    merged_ingredient = merge_ingredient_books(
        stored_ingredient or IngredientBook(), new_ingredient
    )
    # An extraction that found no substances neither downgrades a stored composition
    # category nor writes a type, which would stop the record inheriting its parent's.
    composition = new_type == COMPOSITION_DRIVEN or stored_ingredient is not None
    await save_knowledge(
        category,
        merged_brand,
        merged_model,
        EXTRACT_KNOWLEDGE_MODEL,
        category_type=COMPOSITION_DRIVEN if composition else None,
        ingredient_book=merged_ingredient if composition else None,
        # notes that name no provider say nothing about benchmark: it stays as stored
        capabilities=learned_benchmark(provider) if provider else None,
    )
    shown_type = COMPOSITION_DRIVEN if composition else new_type
    return merged_brand, merged_model, shown_type, merged_ingredient


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
            f"{kzt(change.old_price)}",
            f"{kzt(change.new_price)}",
            f"[green]{kzt(change.delta)}[/green]",
        )
    console.print(table)


@app.command()
def refresh() -> None:
    """Re-scan every tracked category and record fresh price snapshots."""
    results = asyncio.run(run_refresh_tracked())
    if not results:
        console.print(
            "[yellow]No tracked categories yet — run `recommend` first "
            "(or the local store is unavailable).[/yellow]"
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
        table.add_row(snap.captured_at, f"{kzt(snap.min_price)}", str(snap.offer_count))
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
            "(no match, or the local store is unavailable).[/yellow]"
        )
        return
    for entry in entries:
        _render_history(entry)


@app.command("ozon-setup")
def ozon_setup() -> None:
    """Open a visible browser, pass Ozon's anti-bot check once, and save the session."""
    if setup_browser() is None:
        console.print(f"[red]Нет браузера для Ozon: {escape(INSTALL_BROWSER)}[/red]")
        raise typer.Exit(code=1)
    console.print("Откроется окно браузера. Дождитесь результатов поиска Ozon…")
    if not asyncio.run(pass_ozon_check()):
        console.print("[red]Не удалось получить сессию Ozon за отведённое время.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Сессия Ozon сохранена: {escape(str(ozon_state_path()))}[/green]")


llm_app = typer.Typer(help="The LLM key: stored in the OS keyring, never shown.")
app.add_typer(llm_app, name="llm")


def _provider(name: str) -> str:
    if name not in keys.KEY_FIELDS:
        raise typer.BadParameter(f"expected one of {', '.join(keys.KEY_FIELDS)}")
    return name


@llm_app.command("set-key")
def llm_set_key(provider: str = typer.Argument(..., callback=_provider)) -> None:
    """Store an API key for PROVIDER (prompted, not echoed) and make it the one to use."""
    key = typer.prompt(f"{provider} API key", hide_input=True)
    try:
        keys.set_key(provider, key)
    except (keys.KeyStoreError, ValueError) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Saved the {provider} key in the OS keyring.[/green]")
    llm_status()  # an env key still wins over the stored one


@llm_app.command("clear-key")
def llm_clear_key(provider: str = typer.Argument(..., callback=_provider)) -> None:
    """Remove PROVIDER's stored key."""
    try:
        keys.clear_key(provider)
    except keys.KeyStoreError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"Cleared the {provider} key.")
    llm_status()  # an env key or the claude CLI may still serve LLM calls


@llm_app.command("status")
def llm_status() -> None:
    """Where LLM calls go and where the key came from; never the key."""
    check = llm_check()
    console.print(
        f"LLM: {escape(check.detail)}" if check.ok else f"LLM: none. {escape(check.hint)}"
    )


@app.command()
def doctor() -> None:
    """Check what is set up; every missing piece only disables part of the pipeline."""
    for check in doctor_checks():
        if check.ok:
            mark = f"[green]ok[/green]  {escape(check.detail)}"
        else:
            mark = f"[yellow]missing[/yellow]  {escape(check.hint)}"
        console.print(f"{check.name:<20} {mark}".rstrip())


@app.command()
def gui() -> None:
    """Open the desktop app (needs the gui extra)."""
    try:
        from buysmarter.gui.app import run as run_gui
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("PySide6"):
            raise
        hint = escape("pip install 'buysmarter[gui]'")
        console.print(f"[red]The desktop app needs the gui extra: {hint}[/red]")
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=run_gui())


_INTERRUPTED = 130  # typer's exit code for Ctrl+C


def main() -> None:
    """``buysmarter``: the typer app, with a word on Ctrl+C instead of a silent exit."""
    try:
        app()
    except SystemExit as exc:
        if exc.code == _INTERRUPTED:
            # Browser calls cut short by Ctrl+C surface as "Future exception was never retrieved"
            # when the loop is collected; the user asked to stop, so that noise is not news.
            logging.getLogger("asyncio").setLevel(logging.CRITICAL)
            console.print("\n[yellow]Остановлено.[/yellow]")
        raise


if __name__ == "__main__":
    main()
