"""Typer CLI entry point for buy-smarter."""

import asyncio
import functools
import logging
import sys
from pathlib import Path
from typing import Any

import httpx
import typer
from market_sources.ozon.browser import ozon_state_path
from rich.markup import escape
from rich.table import Table

from buysmarter import local_store
from buysmarter.cli.category import category_app
from buysmarter.cli.clarify import CLARIFY_PROMPT as CLARIFY_PROMPT  # re-export
from buysmarter.cli.clarify import EXIT_CLARIFY, print_clarification
from buysmarter.cli.clarify import ask as ask_clarification
from buysmarter.cli.console import (
    console,
    render,
    render_knowledge,
    render_no_substance,
    render_used,
)
from buysmarter.cli.learning import learn_before_search, search_and_follow_up
from buysmarter.cli.prefs import prefs_app
from buysmarter.cli.progress import stages
from buysmarter.cli.report import (
    DEFAULT_TOP,
    knowledge_rows,
    no_new_pick_text,
    save_report,
)
from buysmarter.i18n import kzt
from buysmarter.knowledge.generate import GENERATE_KNOWLEDGE_MODEL, learn_from_notes
from buysmarter.knowledge.generate import learn_category as learn_category_knowledge
from buysmarter.llm import keys
from buysmarter.llm.adapter import LlmError
from buysmarter.logsetup import new_run_id, setup_logging
from buysmarter.memory.prefs import search_defaults
from buysmarter.services.auto_learn import (
    NeedsLearning,
    limit_note,
    over_limit,
)
from buysmarter.services.collect import ALL_SOURCES, select_sources
from buysmarter.services.doctor import (
    INSTALL_BROWSER,
    doctor_checks,
    llm_check,
    pass_ozon_check,
)
from buysmarter.services.ozon_browser import setup_browser
from buysmarter.services.price_tracking import CategoryRefresh, ProductHistory
from buysmarter.services.price_tracking import product_history as run_product_history
from buysmarter.services.price_tracking import refresh_tracked as run_refresh_tracked
from buysmarter.services.recommend import (
    Clarify,
    NoNewPick,
    NoSubstanceMatch,
    RecommendResult,
    stored_budget,
    understand_request,
    with_excluded,
)
from buysmarter.services.recommend import parse_request as run_parse_request
from buysmarter.services.recommend import recommend as run_recommend
from buysmarter.services.understanding import NeedsClarification, NothingToSearch

_DEFAULT_TOP = DEFAULT_TOP

app = typer.Typer(help="buy-smarter: AI-powered shopping assistant.")
logger = logging.getLogger(__name__)


@app.callback()
def _main() -> None:
    """buy-smarter: AI-powered shopping assistant (keeps subcommands named)."""
    setup_logging()
    new_run_id()
    local_store.prepare()


_QUERY_HELP = 'Natural-language request, e.g. "нужен ирригатор, IPX7, до 40000 тг, Алматы"'


@app.command()
def recommend(
    query: str = typer.Argument(..., help=_QUERY_HELP),
    city: str | None = typer.Option(None, "--city", "-c", help="Override city."),
    budget: int | None = typer.Option(None, "--budget", "-b", help="Override max price (KZT)."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Force keyless naive query parsing."),
    trusted_only: bool = typer.Option(
        False, "--trusted-only", help="Reject products from unknown/untrusted brands."
    ),
    sources: str | None = typer.Option(
        None,
        "--sources",
        help="Comma-separated marketplaces to query, e.g. 'kaspi,ozon,olx' or 'kaspi'. "
        "Default: the saved ones (`buysmarter prefs`), else all.",
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
        False, "--learn", help="Learn a new category before searching, without asking."
    ),
) -> None:
    """Recommend the best product for a natural-language request.

    An ambiguous request is asked about first; off a terminal the question is printed and
    the exit code is 3.
    """
    selected = _parse_sources(sources) if sources else None
    use_llm = not no_llm
    search = functools.partial(
        recommend_pipeline,
        query,
        city,
        budget,
        use_llm,
        trusted_only,
        selected,
        with_review_summary=not no_review_summary,
        no_clarify=no_clarify,
    )

    def show(result: RecommendResult) -> None:
        render(result, top, use_llm=use_llm, summary=not no_review_summary)
        if save_path or not no_save:
            path = save_report(result, query, top, save_path)
            console.print(f"[green]Report saved to {escape(str(path))}[/green]")
        if result.knowledge_missing and use_llm and over_limit():
            manual = f"buysmarter learn-category {result.constraints.product_type!r}"
            console.print(
                f"[yellow]{escape(limit_note())} Изучить вручную: {escape(manual)}[/yellow]"
            )

    try:
        asyncio.run(
            search_and_follow_up(search, show, learn=learn, use_llm=use_llm, tty=_on_terminal())
        )
    except NeedsClarification as exc:
        print_clarification(console, exc.clarification)
        raise typer.Exit(code=EXIT_CLARIFY) from exc
    except NothingToSearch as exc:
        console.print(f"[yellow]{escape(str(exc))}[/yellow]")
        raise typer.Exit(code=1) from exc
    except NoSubstanceMatch as exc:
        render_no_substance(exc)
        raise typer.Exit(code=1) from exc
    except NoNewPick as exc:
        text = no_new_pick_text(exc.rejected_reasons, bool(exc.used_listings))
        console.print(f"[yellow]{escape(text)}[/yellow]")
        render_used(exc.used_listings, [])
        raise typer.Exit(code=0 if exc.used_listings else 1) from exc
    except ValueError as exc:
        console.print(f"[yellow]No recommendation: {escape(str(exc))}[/yellow]")
        raise typer.Exit(code=1) from exc
    except httpx.HTTPError as exc:
        console.print(f"[red]Marketplace request failed: {escape(repr(exc))}[/red]")
        raise typer.Exit(code=2) from exc


def _on_terminal() -> bool:
    return sys.stdin.isatty()


def _parse_sources(raw: str) -> list[str]:
    """Parse the ``--sources`` CSV into a validated ordered marketplace list."""
    try:
        return select_sources(raw.split(","))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


_BUDGET_PROMPT = "Бюджет в ₸ (Enter — без лимита)"


def _resolve_budget(
    budget: int | None, parsed_budget: int | None, saved: int | None = None
) -> int | None:
    """Fill in a missing budget interactively, TTY only.

    ``--budget`` wins outright; a budget already found in the query or a ``saved`` one is left
    alone (``recommend`` keeps or falls back to it, no override needed); otherwise ask — but
    only on a real terminal, so scripts/CI never block on stdin.
    """
    if budget is not None or parsed_budget is not None or saved is not None:
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
    learn: bool = False,
    background: dict[str, asyncio.Task[bool]] | None = None,
    settled: dict[str, Any] | None = None,
) -> RecommendResult:
    """Parse once, settle any question, prompt for a missing budget, then recommend.

    On a terminal a clarification is asked here (an option goes back as ``answer``, the
    user's own text joins the query, which is parsed again) and the next round runs; the
    budget typed once holds for every round. Off a terminal ``NeedsClarification`` leaves.
    A new category is learned before the search with ``learn``, or asked about on a terminal;
    a "no" learns it in ``background`` while the search runs. ``settled`` gets the arguments of
    the search that ran, so a repeat asks nothing again.
    """
    saved_sources, excluded = await search_defaults()
    sources = sources or saved_sources or list(ALL_SOURCES)
    parsed = with_excluded(await run_parse_request(query, use_llm), excluded)
    answer, clarify_round, budget_asked = None, 0, False
    learn_first = learn or _on_terminal()
    while True:
        ask = Clarify(answer, clarify_round, no_clarify)
        try:
            if not budget_asked:
                _, understood, _ = await understand_request(query, parsed, use_llm, ask)
                saved = await stored_budget()
                budget, budget_asked = _resolve_budget(budget, understood.budget_max, saved), True
            call: dict[str, Any] = {
                "query": query,
                "city": city,
                "budget": budget,
                "use_llm": use_llm,
                "trusted_only": trusted_only,
                "sources": sources,
                "parsed": parsed,
                "with_review_summary": with_review_summary,
                "answer": answer,
                "clarify_round": clarify_round,
                "no_clarify": no_clarify,
                "learn_first": learn_first,
            }
            if settled is not None:  # for «Повторить поиск»: the same answer, round and budget
                settled.clear()
                settled.update(call)
            with stages(console):
                return await run_recommend(**call)
        except NeedsLearning as exc:
            learn_first = False
            await learn_before_search(exc.category, force=learn, background=background)
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
                parsed = with_excluded(await run_parse_request(query, use_llm), excluded)


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
    render_knowledge(category, knowledge_rows(brand, model, category_type, ingredient))


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
        brand, model, category_type, ingredient = asyncio.run(
            learn_from_notes(Path(file), category)
        )
    except Exception as exc:  # noqa: BLE001 - surface a clean CLI error
        console.print(f"[red]Learning from file failed: {escape(str(exc))}[/red]")
        if isinstance(exc, LlmError):
            console.print(_LEARN_LLM_HINT)
        raise typer.Exit(code=1) from exc
    console.print(
        f"[green]Merged notes from {escape(file)} into knowledge "
        f"for {escape(repr(category))}.[/green]"
    )
    render_knowledge(category, knowledge_rows(brand, model, category_type, ingredient))


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
app.add_typer(prefs_app, name="prefs")
app.add_typer(category_app, name="category")


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
