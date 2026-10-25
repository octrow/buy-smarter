"""The recommendation in the terminal: the console tables and notes of ``buysmarter recommend``."""

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from buysmarter.cli.report import (
    USED_HEADERS,
    assessed_note,
    budget_text,
    failed_source_notes,
    found_counts,
    found_line,
    hidden_note,
    ingredients_unchecked_note,
    knowledge_rows,
    rejected_line,
    request_notes,
    skipped_llm_note,
    summary_note,
    unavailable_sources_note,
    used_cells,
    used_first,
)
from buysmarter.i18n import (
    BEST_ROLE,
    ROLE_LABELS,
    USED_SECTION,
    axis_label,
    checked_brands,
    clean_title,
    explain,
    kzt,
    pro_con,
    source_label,
)
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.ingredients import IngredientBook
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.llm.review_summary import ReviewSummary
from buysmarter.ranking.models import Candidate, QueryConstraints, ScoredCandidate
from buysmarter.ranking.selection import distinct_variants, is_barred
from buysmarter.services.matching import UsedListing
from buysmarter.services.recommend import NoSubstanceMatch, RecommendResult

console = Console()
_MONEY_COLUMNS = {"Цена", "Дешевле нового на"}


def _render_constraints(c: QueryConstraints, city_defaulted: bool = False) -> None:
    city_note = " (по умолчанию)" if city_defaulted else ""
    lines = [
        f"[bold]Категория:[/bold] {escape(c.search_phrase)}",
        f"[bold]Город:[/bold] {escape(c.city or '-')}{city_note}",
        f"[bold]Бюджет:[/bold] {budget_text(c)}",
    ]
    if c.required_features:
        lines.append(f"[bold]Нужно:[/bold] {escape(', '.join(c.required_features))}")
    if c.excluded_brands:
        lines.append(f"[bold]Без брендов:[/bold] {escape(', '.join(c.excluded_brands))}")
    console.print(Panel("\n".join(lines), title="Запрос", expand=False))


def _render_best(best: ScoredCandidate) -> None:
    cand = best.candidate
    # M4: `best` can be a barred (suspicious-price/dup-gap/mimicry/
    # counterfeit-verdict) candidate ONLY via assemble_recommendation's
    # all-barred fallback — nothing trustworthy was left to prefer. That must
    # be visible, not silently presented as an endorsed pick.
    barred = is_barred(best)
    title = f"⚠ {clean_title(cand.title)}" if barred else clean_title(cand.title)
    header = (
        f"🥇 [bold]{escape(title)}[/bold]  [dim]\\[{source_label(cand.source)}][/dim]\n"
        f"Баллы: [bold]{best.total_score:.0f}[/bold]/100   "
        f"Уверенность: [bold]{best.confidence:.0%}[/bold]\n"
        f"Цена: [bold]{kzt(cand.price)}[/bold]"
    )
    if barred:
        header += "\n[bold red]все кандидаты под подозрением — проверьте вручную[/bold red]"
    if best.review_penalty > 0:
        header += f"\n[red]Штраф: отзывы (−{best.review_penalty:.0f})[/red]"
    if cand.best_seller:
        header += f"\nПродавец: {escape(cand.best_seller)}"
    if cand.url:
        header += f"\n{escape(cand.url)}"
    console.print(Panel(header, title=BEST_ROLE, expand=False))

    table = Table(show_header=True, header_style="bold")
    table.add_column("Ось")
    table.add_column("Баллы", justify="right")
    table.add_column("Почему")
    for axis in best.axes:
        table.add_row(
            axis_label(axis.name),
            f"{axis.score:.1f}/{axis.max_score:.0f}",
            escape(explain(axis.explanation)),
        )
    console.print(table)

    if best.pros:
        console.print("[green]+ " + escape("  + ".join(map(pro_con, best.pros))) + "[/green]")
    if best.cons:
        console.print("[red]- " + escape("  - ".join(map(pro_con, best.cons))) + "[/red]")


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
    table = Table(title="Другие варианты", show_header=True, header_style="bold")
    table.add_column("Роль")
    table.add_column("Маркетплейс")
    table.add_column("Товар")
    table.add_column("Цена", justify="right")
    table.add_column("Баллы", justify="right")
    for role, scored in alts.items():
        key = scored.candidate.source_product_id
        if key in seen:
            continue
        seen.add(key)
        table.add_row(
            ROLE_LABELS.get(role, role),
            source_label(scored.candidate.source),
            _product_cell(clean_title(scored.candidate.title), scored.candidate.url),
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
    title = f"Лучшие предложения (топ {len(ranked)})"
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Маркетплейс")
    table.add_column("Товар")
    table.add_column("Цена", justify="right")
    table.add_column("Баллы", justify="right")
    table.add_column("Бренд")
    for i, s in enumerate(ranked, start=1):
        c = s.candidate
        # A barred candidate (suspicious price, dup-price-gap, mimicry, or a
        # review-summary counterfeit verdict) stays visible in this table but
        # gets a visible "⚠" marker — it can never win best/alternative roles
        # (docs/PLAN.md "Ranking-фиксы"), and the table shouldn't hide that.
        title_text = f"⚠ {clean_title(c.title)}" if is_barred(s) else clean_title(c.title)
        table.add_row(
            str(i),
            source_label(c.source),
            _product_cell(title_text, c.url),
            f"{kzt(c.price)}",
            f"{s.total_score:.0f}",
            _tier_cell(c),
        )
    console.print(table)


def render_used(used: list[UsedListing], ranked: list[ScoredCandidate]) -> None:
    if not used:
        return
    brands = checked_brands(s.candidate for s in ranked)
    table = Table(title=USED_SECTION, show_header=True, header_style="bold")
    for header in USED_HEADERS:
        money = header in _MONEY_COLUMNS
        table.add_column(
            header, justify="right" if money else "left", no_wrap=money, overflow="fold"
        )
    for listing in used:
        title, *rest = used_cells(listing, brands)
        table.add_row(_product_cell(title, listing.url), *map(escape, rest))
    console.print(table)


def _render_hidden(result: RecommendResult) -> None:
    """The suspected fakes "только оригинал" hid, so a wrongly hidden product can be found."""
    if note := hidden_note(result):
        console.print(f"[yellow]{escape(note)}[/yellow]")
        for scored in result.hidden:
            console.print(f"[dim]  {escape(clean_title(scored.candidate.title))}[/dim]")


def render(
    result: RecommendResult, top: int, *, use_llm: bool = True, summary: bool = True
) -> None:
    """The report in the terminal; ``use_llm``/``summary`` say why a review summary is missing."""
    if result.restricted_note:  # always the first line
        console.print(f"[bold yellow]⚠ {escape(result.restricted_note)}[/bold yellow]")
    console.print(found_line(found_counts(result)))
    _render_constraints(result.constraints, result.city_defaulted)
    notes = (
        *request_notes(result),
        assessed_note(result),
        skipped_llm_note(result),
        unavailable_sources_note(result),
        *failed_source_notes(result),
        summary_note(result, use_llm=use_llm, asked=summary),
        ingredients_unchecked_note(result),
    )
    for note in notes:
        if note:
            console.print(f"[yellow]{escape(note)}[/yellow]")
    used = (result.used_listings, result.recommendation.ranked)
    if used_first(result):
        render_used(*used)
    _render_best(result.recommendation.best)
    _render_review_summary(result.review_summary)
    _render_alternatives(result.recommendation.alternatives)
    _render_top(result.recommendation.ranked, top)
    if not used_first(result):
        render_used(*used)
    _render_hidden(result)
    if result.recommendation.rejected_count:
        console.print(f"[dim]{escape(rejected_line(result))}[/dim]")


def render_no_substance(exc: NoSubstanceMatch) -> None:
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
    table.add_column("Товар")
    table.add_column("Цена", justify="right")
    table.add_column("Рейтинг", justify="right")
    for cand in exc.alternatives:
        rating = f"{cand.rating:.1f}" if cand.rating is not None else "-"
        table.add_row(_product_cell(cand.title, cand.url), f"{kzt(cand.price)}", rating)
    console.print(table)


def render_knowledge(
    category: str,
    brand: BrandBook,
    model: ModelBook,
    category_type: str = "spec_driven",
    ingredient: IngredientBook | None = None,
) -> None:
    """Print a tiers-and-models summary table for a category's knowledge."""
    rows = knowledge_rows(brand, model, category_type, ingredient)
    table = Table(title=f"Brand tiers — {category}", show_header=True, header_style="bold")
    table.add_column("Tier")
    table.add_column("N", justify="right")
    table.add_column("Brands")
    for tier, brands in rows.tiers:
        table.add_row(tier, str(len(brands)), escape(", ".join(brands)) or "-")
    console.print(table)
    console.print(
        f"[bold]Type:[/bold] {rows.category_type}   "
        f"[bold]Models:[/bold] {rows.models}   "
        f"[bold]Brands:[/bold] {rows.brands}   "
        f"[bold]Aliases:[/bold] {rows.aliases}"
    )
    if not rows.substances:
        return
    table = Table(title="Active substances", show_header=True, header_style="bold")
    table.add_column("Substance")
    table.add_column("Concentrations")
    table.add_column("Equivalents")
    for name, concentrations, equivalents in rows.substances:
        table.add_row(name, ", ".join(concentrations) or "-", ", ".join(equivalents) or "-")
    console.print(table)
