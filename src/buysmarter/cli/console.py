"""The recommendation in the terminal: the console tables and notes of ``buysmarter recommend``."""

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from buysmarter.cli.report import (
    ROLE_LABELS,
    assessed_note,
    budget_text,
    format_rejected_reasons,
    hidden_note,
    request_notes,
    skipped_llm_note,
    unavailable_sources_note,
    used_first,
)
from buysmarter.i18n import USED_SECTION, checked_brands, clean_title, kzt, used_con
from buysmarter.llm.review_summary import ReviewSummary
from buysmarter.ranking.models import Candidate, QueryConstraints, ScoredCandidate
from buysmarter.ranking.selection import distinct_variants, is_barred
from buysmarter.services.matching import UsedListing
from buysmarter.services.recommend import NoSubstanceMatch, RecommendResult

console = Console()


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


def render_used(used: list[UsedListing], ranked: list[ScoredCandidate]) -> None:
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


def _render_hidden(result: RecommendResult) -> None:
    """The suspected fakes "только оригинал" hid, so a wrongly hidden product can be found."""
    if note := hidden_note(result):
        console.print(f"[yellow]{escape(note)}[/yellow]")
        for scored in result.hidden:
            console.print(f"[dim]  {escape(clean_title(scored.candidate.title))}[/dim]")


def render(result: RecommendResult, top: int) -> None:
    if result.restricted_note:
        console.print(f"[bold yellow]⚠ {escape(result.restricted_note)}[/bold yellow]")
    _render_constraints(result.constraints, result.city_defaulted)
    notes = (
        *request_notes(result),
        assessed_note(result),
        skipped_llm_note(result),
        unavailable_sources_note(result),
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
        console.print(
            f"[dim]{result.recommendation.rejected_count} candidate(s) "
            "rejected by hard filters.[/dim]"
        )
        if result.recommendation.rejected_reasons:
            breakdown = format_rejected_reasons(result.recommendation.rejected_reasons)
            console.print(f"[dim]Отсеяно: {breakdown}[/dim]")


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
    table.add_column("Product")
    table.add_column("Price", justify="right")
    table.add_column("Rating", justify="right")
    for cand in exc.alternatives:
        rating = f"{cand.rating:.1f}" if cand.rating is not None else "-"
        table.add_row(_product_cell(cand.title, cand.url), f"{kzt(cand.price)}", rating)
    console.print(table)
