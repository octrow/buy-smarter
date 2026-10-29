"""Markdown rendering of a recommendation, shared by ``--save`` (and mirrors the
rich console output). Pure string building — no I/O, no rich, no network.

The console renderer in ``cli.main`` and this markdown renderer show the same
content (parsed request, best pick + axes, alternatives, top candidates, used
listings); this is the single source of the report body written to disk — the CLI's
``--save``, the desktop app (``gui.app``), the worker and MCP all write it. Russian, from the
same ``buysmarter.i18n`` tables the GUI shows.
"""

import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import httpx

from buysmarter.capabilities.provenance import assessed_entries, assessed_line
from buysmarter.capabilities.used_only import USED
from buysmarter.i18n import (
    AXIS_SHORT,
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
    used_con,
)
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.ingredients import IngredientBook
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.llm.review_summary import ReviewSummary
from buysmarter.ranking.models import AxisScore, Candidate, QueryConstraints, ScoredCandidate
from buysmarter.ranking.selection import distinct_variants, is_barred
from buysmarter.services.collect import ALL_SOURCES
from buysmarter.services.matching import UsedListing
from buysmarter.services.recommend import NoNewPick, NoSubstanceMatch, RecommendResult
from buysmarter.services.understanding import Clarification

__all__ = ["ROLE_LABELS"]  # re-exported for the console renderer
REPORT_TITLE = "# buy-smarter: рекомендация"
_REPORTS_DIR = Path.home() / ".local" / "share" / "buysmarter" / "reports"
DEFAULT_TOP = 8  # ranked candidates in a report's table (``search --top``)

# M5 (docs/PLAN.md "Ranking-фиксы"): ``Recommendation.rejected_reasons`` keys
# are stable English prefixes (``ranking.engine._reason_key``), chosen for
# aggregation, not display. Translate at render time only — an unknown key
# (a reason shape this map hasn't caught up with yet) passes through as-is
# rather than disappearing.
_REASON_LABELS_RU = {
    "price exceeds budget": "дороже бюджета",
    "not available": "нет в наличии",
    "excluded brand": "исключённый бренд",
    "missing required feature(s)": "нет требуемой функции",
    "unknown brand (trusted-brands-only)": "неизвестный бренд",
    "other audience": "для другой аудитории (пол/возраст)",
    "other size": "другой размер",
    "excluded substance": "содержит исключённое вещество",
    "suspected counterfeit": "подозрение на подделку",
}


def format_rejected_reasons(reasons: dict[str, int]) -> str:
    """Compact "label ×N, label ×N" breakdown, Russian-labeled where known,
    ordered by count descending (ties keep ``Counter``'s insertion order)."""
    return ", ".join(
        f"{_REASON_LABELS_RU.get(reason, reason)} ×{n}"
        for reason, n in Counter(reasons).most_common()
    )


def rejected_line(result: RecommendResult) -> str:
    """How many candidates the hard filters dropped, then why, as the app and the console say."""
    rec = result.recommendation
    line = f"Отсеяно жёсткими фильтрами: {rec.rejected_count}."
    if rec.rejected_reasons:
        line += f"\n{format_rejected_reasons(rec.rejected_reasons)}"
    return line


def no_new_pick_text(reasons: dict[str, int], has_used: bool, *, olx: bool = True) -> str:
    """What to say when no new product passed: why, and whether OLX has something."""
    head = f"Новых предложений нет: {format_rejected_reasons(reasons)}."
    if has_used:
        return f"{head} На OLX нашлось:"
    return f"{head} На OLX тоже ничего." if olx else head


def marketplace_error_text(exc: httpx.HTTPError) -> str:
    """A marketplace request that failed, for the user: which one and why, no internals."""
    try:
        host = exc.request.url.host
    except RuntimeError:  # an error raised without a request
        host = ""
    name = next((source_label(s) for s in ALL_SOURCES if s in host), host or "Маркетплейс")
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{name} не ответил: ошибка HTTP {exc.response.status_code}. Попробуйте позже."
    return f"{name} не отвечает: нет связи. Попробуйте позже."


def no_recommendation_text(exc: ValueError) -> str:
    """The core's other "nothing to recommend" (a ``ValueError``), in the CLI and the app."""
    return f"Не нашлось подходящих предложений: {exc}"


def no_new_pick_message(exc: NoNewPick) -> str:
    """``no_new_pick_text`` for the core's ``NoNewPick``: OLX is named only if it was searched."""
    return no_new_pick_text(exc.rejected_reasons, bool(exc.used_listings), olx=exc.olx_searched)


NO_SUBSTANCE_TITLE = "Ближайшие альтернативы (без требуемого вещества)"
NO_SUBSTANCE_HEADERS = ["Товар", "Цена", "Рейтинг"]


def no_substance_cells(cand: Candidate) -> list[str]:
    """A product without the required substance, as the CLI's and the app's table show it."""
    rating = f"{cand.rating:.1f}" if cand.rating is not None else "—"
    return [clean_title(cand.title), kzt(cand.price), rating]


_fmt_price = kzt  # the GUI's "35 000 ₸" (no-break space), never "35,000"


def _md_link(title: str, url: str | None) -> str:
    """Markdown ``[title](url)`` link, or the plain title when there's no URL."""
    return f"[{title}]({url})" if url else title


def hidden_note(result: RecommendResult) -> str | None:
    """The note on the fakes "только оригинал" took out, or None.

    "Скрыто подозрительных предложений (только оригинал): N" keeps the noun clear of the number.
    """
    if not result.hidden:
        return None
    return f"Скрыто подозрительных предложений (только оригинал): {len(result.hidden)}."


def _hidden_md(result: RecommendResult) -> list[str]:
    note = hidden_note(result)
    if note is None:
        return []
    items = [
        f"- {_md_link(clean_title(s.candidate.title), s.candidate.url)}" for s in result.hidden
    ]
    return ["", f"_{note}_", "", *items]


def skipped_llm_note(result: RecommendResult) -> str | None:
    """The steps a keyless run did without LLM, or None when the LLM ran (or was opted out)."""
    if not result.skipped_llm_steps:
        return None
    return (
        "AI не настроен: запрос разобран по ключевым словам, без сводки отзывов и разбора "
        "состава. Ключ добавляется командой `buysmarter llm set-key <provider>`."
    )


def ingredients_unchecked_note(result: RecommendResult) -> str | None:
    """How many ranked candidates' состав went unchecked, or None when all were checked."""
    if not result.ingredients_unchecked:
        return None
    ranked = result.recommendation.ranked
    unchecked = sum(not s.candidate.ingredients_checked for s in ranked)
    return f"Состав не проверен у {unchecked} из {len(ranked)} — вещество не подтверждено."


def unavailable_sources_note(result: RecommendResult) -> str | None:
    """The marketplaces left out for lack of a browser, or None when every selected one ran."""
    if not result.unavailable_sources:
        return None
    names = ", ".join(map(source_label, result.unavailable_sources))
    return f"Пропущено ({names}): нет браузера. Что установить, подскажет `buysmarter doctor`."


def found_counts(result: RecommendResult) -> Counter[str]:
    """How many ranked offers each marketplace brought; OLX counts its ads."""
    counts = Counter(s.candidate.source for s in result.recommendation.ranked)
    counts["olx"] += len(result.used_listings)
    return counts


def found_line(counts: Counter[str]) -> str:
    """ "Найдено предложений: N, б/у на OLX: M." under the search, in the app and the console."""
    new = sum(n for name, n in counts.items() if name != "olx")
    olx = f", б/у на OLX: {counts['olx']}" if counts["olx"] else ""
    return f"Найдено предложений: {new}{olx}."


def failed_source_notes(result: RecommendResult) -> list[str]:
    """A line per marketplace whose search failed, as the app's «Не удалось проверить»."""
    return [
        f"{source_label(name)}: ошибка — его предложений нет в отчёте (подробности в логе)."
        for name in result.failed_sources
    ]


def summary_note(result: RecommendResult, *, use_llm: bool, asked: bool = True) -> str | None:
    """Why the report has no review summary, or None when it has one or was not ``asked`` for.

    A keyless run says so in ``skipped_llm_note``; the AI turned off (``--no-llm``, the app's
    checkbox) and a summary that did not come out (``review_summary_note``) are said here.
    """
    if result.skipped_llm_steps:
        return None
    if not use_llm:
        return "AI выключен: запрос разобран по ключевым словам, сводки отзывов нет."
    if result.review_summary is not None or not asked:
        return None
    why = result.review_summary_note
    return f"Сводка отзывов недоступна: {why}." if why else "Сводка отзывов недоступна."


def brands_missing_note(result: RecommendResult) -> str | None:
    """Why every brand ranks as unknown: the category has no brand knowledge (the app's banner)."""
    if not result.knowledge_missing:
        return None
    category = result.constraints.product_type or result.constraints.search_phrase
    return (
        f"Для категории «{category}» нет данных о брендах, поэтому все бренды оценены как "
        "неизвестные и ось «Бренд» никого не выделяет. Это исправит изучение категории (AI с "
        "поиском в интернете, 1–3 минуты); после него повторите поиск."
    )


def report_notes(result: RecommendResult, *, use_llm: bool, summary: bool) -> list[str]:
    """The notes under the request, in the console, the markdown report and the app alike."""
    notes = (
        *request_notes(result),
        assessed_note(result),
        skipped_llm_note(result),
        unavailable_sources_note(result),
        *failed_source_notes(result),
        summary_note(result, use_llm=use_llm, asked=summary),
        ingredients_unchecked_note(result),
        brands_missing_note(result),
    )
    return [note for note in notes if note]


def assessed_note(result: RecommendResult) -> str | None:
    """ "Как оценивали: состав — обучение." for every render, or None when nothing took part."""
    return assessed_line(assessed_entries(result.capabilities))


USED_CONDITION_NOTE = "Вы искали б/у: объявления с OLX идут первым разделом, новые товары ниже."


def used_first(result: RecommendResult) -> bool:
    """True when the query asked for second-hand goods: the used section leads every report."""
    return result.constraints.condition == USED


def request_notes(result: RecommendResult) -> list[str]:
    """What the run read differently from the words: the assumed option, budget, condition."""
    c = result.constraints
    notes = []
    if result.assumed:
        notes.append(f"Искал: {result.assumed} — уточните запрос, если нужно другое.")
    if c.budget_note:
        notes.append(f"{c.budget_note[:1].upper()}{c.budget_note[1:]}.")
    if used_first(result):
        notes.append(USED_CONDITION_NOTE)
    return notes


def budget_text(c: QueryConstraints) -> str:
    """The budget line: "без лимита", "до N" or "от N до M"."""
    if not c.budget_max:
        return "без лимита"
    if c.budget_min:
        return f"от {_fmt_price(c.budget_min)} до {_fmt_price(c.budget_max)}"
    return _fmt_price(c.budget_max)


def _constraints_md(result: RecommendResult, *, use_llm: bool, summary: bool) -> list[str]:
    c = result.constraints
    city_note = " (по умолчанию)" if result.city_defaulted else ""
    lines = [
        "## Запрос",
        "",
        f"- **Категория:** {c.search_phrase}",
        f"- **Город:** {c.city or '-'}{city_note}",
        f"- **Бюджет:** {budget_text(c)}",
    ]
    if c.required_features:
        lines.append(f"- **Нужно:** {', '.join(c.required_features)}")
    if c.excluded_brands:
        lines.append(f"- **Без брендов:** {', '.join(c.excluded_brands)}")
    for note in report_notes(result, use_llm=use_llm, summary=summary):
        lines += ["", f"_{note}_"]
    return lines


def _best_md(result: RecommendResult) -> list[str]:
    best = result.recommendation.best
    cand = best.candidate
    # M4: `best` is barred ONLY via assemble_recommendation's all-barred
    # fallback (nothing trustworthy left to prefer) — must be visible.
    barred = is_barred(best)
    title = clean_title(cand.title)
    title = f"⚠ {title}" if barred else title
    lines = [
        "",
        f"## {BEST_ROLE}",
        "",
        f"### 🥇 {title}  [{source_label(cand.source)}]",
        "",
        f"- **Баллы:** {best.total_score:.0f}/100",
        f"- **Уверенность:** {best.confidence:.0%}",
        f"- **Цена:** {_fmt_price(cand.price)}",
    ]
    if barred:
        lines.append("- **⚠ все кандидаты под подозрением — проверьте вручную**")
    if best.review_penalty > 0:
        lines.append(f"- **Штраф:** отзывы (−{best.review_penalty:.0f})")
    if cand.best_seller:
        lines.append(f"- **Продавец:** {cand.best_seller}")
    if cand.url:
        lines.append(f"- **Ссылка:** {cand.url}")
    lines += ["", "| Ось | Баллы | Почему |", "| --- | ---: | --- |"]
    for axis in best.axes:
        lines.append(
            f"| {axis_label(axis.name)} | {axis.score:.1f}/{axis.max_score:.0f} "
            f"| {explain(axis.explanation)} |"
        )
    if best.pros:
        lines += ["", "**+** " + "  **+** ".join(map(pro_con, best.pros))]
    if best.cons:
        lines += ["", "**-** " + "  **-** ".join(map(pro_con, best.cons))]
    return lines


SUMMARY_TITLE = "Отзывы кратко"
_SUMMARY_MARKS = {
    "flag": "⚠ {}",
    "pro": "✓ {}",
    "con": "✗ {}",
    "complaint": "«{}»",
    "verdict": "{}",
}


def summary_lines(summary: ReviewSummary) -> list[tuple[str, str]]:
    """The review summary as ``(kind, text)`` lines, in order: red flags, pros, cons, frequent
    complaints, the verdict; the console, the markdown and the app colour them by ``kind``."""
    groups = (
        ("flag", summary.red_flags),
        ("pro", summary.pros),
        ("con", summary.cons),
        ("complaint", summary.common_complaints),
        ("verdict", [summary.verdict_line] if summary.verdict_line else []),
    )
    return [(kind, _SUMMARY_MARKS[kind].format(text)) for kind, texts in groups for text in texts]


def _review_summary_md(result: RecommendResult) -> list[str]:
    said = summary_lines(result.review_summary) if result.review_summary else []
    if not said:
        return []
    lines = ["", f"## {SUMMARY_TITLE}", ""]
    lines += [f"- {text}" for kind, text in said if kind != "verdict"]
    lines += [f"\n**{text}**" for kind, text in said if kind == "verdict"]
    return lines


def _alternatives_md(result: RecommendResult) -> list[str]:
    alts = result.recommendation.alternatives
    if not alts:
        return []
    lines = [
        "",
        "## Другие варианты",
        "",
        "| Роль | Маркетплейс | Товар | Цена | Баллы |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    seen: set[str] = set()
    for role, scored in alts.items():
        key = scored.candidate.source_product_id
        if key in seen:
            continue
        seen.add(key)
        c = scored.candidate
        lines.append(
            f"| {ROLE_LABELS.get(role, role)} | {source_label(c.source)} | "
            f"{_md_link(clean_title(c.title), c.url)} | "
            f"{_fmt_price(c.price)} | {scored.total_score:.0f} |"
        )
    return lines


def tier_cell(c: Candidate) -> str:
    """Tier column: a mimic candidate flags the counterfeit signal instead of
    the (usually empty) tier — "—" alone would hide the more useful fact."""
    if c.mimic_brand is not None:
        return f"подделка под {c.mimic_brand}?"
    return c.brand_tier or "—"


# Axes whose reason is the offer's own (which sample it looks like): every row says it,
# not only the best pick (the app: the score cell's tooltip).
ROW_REASONS = frozenset({"style"})


def row_reason(axis: AxisScore) -> str:
    """The axis's reason when it is in ``ROW_REASONS``, else empty."""
    return explain(axis.explanation) if axis.name in ROW_REASONS and axis.explanation else ""


def axes_cell(scored: ScoredCandidate) -> str:
    """An offer's axis scores in one cell: the app's per-axis columns, one letter each."""
    return " · ".join(_axis_short(a) for a in scored.axes)


def _axis_short(axis: AxisScore) -> str:
    why = row_reason(axis)
    return f"{AXIS_SHORT.get(axis.name, axis.name)} {axis.score:.1f}" + (f" ({why})" if why else "")


def axes_legend(ranked: list[ScoredCandidate]) -> str:
    """What each letter of ``axes_cell`` stands for, for the axes these offers have."""
    names = dict.fromkeys(a.name for scored in ranked for a in scored.axes)
    pairs = ", ".join(f"{AXIS_SHORT.get(n, n)} — {axis_label(n)}" for n in names)
    return f"Оси: {pairs}."


def _top_md(result: RecommendResult, top: int) -> list[str]:
    ranked = distinct_variants(result.recommendation.ranked, top)
    if not ranked:
        return []
    lines = [
        "",
        f"## Лучшие предложения (топ {len(ranked)})",
        "",
        "| # | Маркетплейс | Товар | Цена | Баллы | Оси | Бренд |",
        "| ---: | --- | --- | ---: | ---: | --- | --- |",
    ]
    for i, scored in enumerate(ranked, start=1):
        c = scored.candidate
        # See cli.main._render_top: a barred candidate (suspicious price,
        # dup-price-gap, mimicry, review-summary counterfeit) stays in the
        # table but gets a visible "⚠" marker — it can never win any role.
        title_text = clean_title(c.title)
        title_text = f"⚠ {title_text}" if is_barred(scored) else title_text
        lines.append(
            f"| {i} | {source_label(c.source)} | {_md_link(title_text, c.url)} | "
            f"{_fmt_price(c.price)} | "
            f"{scored.total_score:.0f} | {axes_cell(scored)} | {tier_cell(c)} |"
        )
    return [*lines, "", f"_{axes_legend(ranked)}_"]


COMPARED_HEADER = "Сравнили с"
USED_HEADERS = ["Объявление", "Цена", "Дешевле нового на", COMPARED_HEADER, "Город", "Минус"]


def used_cells(listing: UsedListing, brands: set[str]) -> list[str]:
    """An OLX ad as the app and the console show it, one cell per ``USED_HEADERS`` column."""
    title = f"{listing.title} → {listing.canonical}" if listing.canonical else listing.title
    standalone = listing.standalone or not listing.matched_title
    saving = "—" if listing.standalone else kzt(listing.saving)
    compared = "—" if standalone else clean_title(listing.matched_title or "")
    con = used_con(listing, brands)
    return [title, kzt(listing.price), saving, compared, listing.city or "—", con]


def _used_row(listing: UsedListing, brands: set[str]) -> str:
    title, *rest = used_cells(listing, brands)
    return "| " + " | ".join([_md_link(title, listing.url), *rest]) + " |"


def _used_md(result: RecommendResult) -> list[str]:
    brands = checked_brands(s.candidate for s in result.recommendation.ranked)
    return _used_table(result.used_listings, brands)


def _used_table(used: list[UsedListing], brands: set[str]) -> list[str]:
    if not used:
        return []
    lines = [
        "",
        f"## {USED_SECTION}",
        "",
        "| " + " | ".join(USED_HEADERS) + " |",
        "| --- | ---: | ---: | --- | --- | --- |",
    ]
    lines += [_used_row(listing, brands) for listing in used]
    return lines


def render_markdown(
    result: RecommendResult, top: int = 8, *, use_llm: bool = True, summary: bool = True
) -> str:
    """Render the whole recommendation as a markdown report string.

    ``use_llm`` and ``summary`` are the run's AI and «Сводка отзывов» choices, so the file says
    why a summary is missing as the screen does (``summary_note``).
    """
    parts = [REPORT_TITLE, ""]
    if result.restricted_note:
        parts += [f"> ⚠ {result.restricted_note}", ""]
    parts += _constraints_md(result, use_llm=use_llm, summary=summary)
    if used_first(result):
        parts += _used_md(result)
    parts += _best_md(result)
    parts += _review_summary_md(result)
    parts += _alternatives_md(result)
    parts += _top_md(result, top)
    if not used_first(result):
        parts += _used_md(result)
    parts += _hidden_md(result)
    rejected = result.recommendation.rejected_count
    if rejected:
        parts += ["", f"_Не прошли фильтры: {rejected}._"]
        reasons = result.recommendation.rejected_reasons
        if reasons:
            parts += [f"_Отсеяно: {format_rejected_reasons(reasons)}_"]
    return "\n".join(parts) + "\n"


def render_clarification_markdown(clarification: Clarification) -> str:
    """A question instead of a ranking: the warning, the question, the numbered options."""
    lines = [REPORT_TITLE, ""]
    if clarification.restricted_note:
        lines += [f"> ⚠ {clarification.restricted_note}", ""]
    lines.append(clarification.question)
    if clarification.options:
        lines += ["", *(f"{n}. {o}" for n, o in enumerate(clarification.options, 1))]
    return "\n".join(lines) + "\n"


def render_no_substance_markdown(exc: NoSubstanceMatch) -> str:
    """Markdown for the honest empty-pool case (mirrors the CLI console output)."""
    lines = [
        REPORT_TITLE,
        "",
        exc.message(),
    ]
    if exc.alternatives:
        lines += ["", "| " + " | ".join(NO_SUBSTANCE_HEADERS) + " |", "| --- | ---: | ---: |"]
        for cand in exc.alternatives:
            title, *rest = no_substance_cells(cand)
            lines.append("| " + " | ".join([_md_link(title, cand.url), *rest]) + " |")
    return "\n".join(lines) + "\n"


def render_no_new_pick_markdown(exc: NoNewPick) -> str:
    """Markdown for "no new product passed": the reasons and the OLX ads within the budget."""
    text = no_new_pick_message(exc)
    lines = [REPORT_TITLE, "", text, *_used_table(exc.used_listings, set())]
    return "\n".join(lines) + "\n"


def _slug(text: str) -> str:
    """URL/file-safe slug from a query (letters/digits → '-', trimmed)."""
    slug = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-")
    return slug[:40] or "report"


def default_report_path(query: str) -> Path:
    """``~/.local/share/buysmarter/reports/YYYYMMDD-HHMMSS-<query-slug>.md`` for now."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return _REPORTS_DIR / f"{stamp}-{_slug(query)}.md"


# The query, whole, as the report's first line: the file name keeps only its lower-case slug.
_QUERY_LINE = re.compile(r"^<!-- query: (.*) -->$")
# ``default_report_path``: YYYYMMDD-HHMMSS-<query-slug>.md
_NAME = re.compile(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}-(.*)$")


def _query_line(query: str) -> str:
    return f"<!-- query: {query.replace('-->', '->')} -->\n"


def _split(path: Path) -> tuple[str | None, str]:
    """``(saved query or None, the report)`` of a report file."""
    text = path.read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    match = _QUERY_LINE.match(first)
    return (match.group(1), rest) if match else (None, text)


def report_text(path: Path) -> str:
    """The report to read: the file without its query line."""
    return _split(path)[1]


def report_parts(path: Path) -> tuple[str, str]:
    """``("21.10.2026 20:38", "Смартфон до 200 000, Алматы!")``: when, and the query searched.

    A report saved before the query line gives its query from the file name: lower-case and
    without punctuation.
    """
    m = _NAME.match(path.stem)
    if m is None:
        return "", path.stem
    year, month, day, hour, minute, slug = m.groups()
    try:
        saved = _split(path)[0]
    except OSError:
        saved = None
    return f"{day}.{month}.{year} {hour}:{minute}", saved or slug.replace("-", " ")


def report_label(path: Path) -> str:
    """``21.10.2026 20:38 — Смартфон до 200 000, Алматы!``: the list entry of a saved report."""
    when, query = report_parts(path)
    return f"{when} — {query}" if when else query


def saved_reports() -> list[Path]:
    """Every report ``save_report`` wrote to the default folder, newest first."""
    if not _REPORTS_DIR.is_dir():
        return []
    return sorted(_REPORTS_DIR.glob("*.md"), key=lambda p: p.name, reverse=True)


def save_report(
    result: RecommendResult,
    query: str,
    top: int,
    path: str | None,
    *,
    use_llm: bool = True,
    summary: bool = True,
) -> Path:
    """Write the markdown report; return the path written. Creates parent dirs."""
    target = Path(path) if path else default_report_path(query)
    target.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(result, top, use_llm=use_llm, summary=summary)
    target.write_text(_query_line(query) + markdown, encoding="utf-8")
    return target


_SHOWN_TIERS = ("A", "B", "C")


class KnowledgeRows(NamedTuple):
    """A category's knowledge summary: the console after ``learn`` and the «Категории» screen."""

    tiers: list[tuple[str, list[str]]]
    category_type: str
    models: int
    brands: int
    aliases: int
    substances: list[tuple[str, list[str], list[str]]]  # name, concentrations, equivalents


def knowledge_rows(
    brand: BrandBook,
    model: ModelBook,
    category_type: str = "spec_driven",
    ingredient: IngredientBook | None = None,
) -> KnowledgeRows:
    """Brand tiers A–C, the counts, and the active substances of a composition category."""
    tiers = [
        (tier, [name for name, of in brand.tiers.items() if of == tier]) for tier in _SHOWN_TIERS
    ]
    substances = [
        (sub.canonical, list(sub.typical_concentrations), [e.name for e in sub.equivalents])
        for sub in (ingredient.substances if ingredient else [])
    ]
    return KnowledgeRows(
        tiers, category_type, len(model.entries), len(brand.tiers), len(brand.aliases), substances
    )


TIERS_TITLE = "Тиры брендов"
TIER_HEADERS = ("Тир", "Брендов", "Бренды")
SUBSTANCES_TITLE = "Активные вещества"
SUBSTANCE_HEADERS = ("Вещество", "Концентрации", "Аналоги")
_KIND = {"composition_driven": "Оценка по составу", "spec_driven": "Оценка по характеристикам"}


def knowledge_line(rows: KnowledgeRows) -> str:
    """How the category is judged and how much it knows, under the tiers."""
    kind = _KIND.get(rows.category_type, rows.category_type)
    return (
        f"{kind} · моделей: {rows.models} · брендов: {rows.brands} · "
        f"других написаний брендов: {rows.aliases}"
    )


def tier_cells(rows: KnowledgeRows) -> list[list[str]]:
    return [[tier, str(len(brands)), ", ".join(brands) or "—"] for tier, brands in rows.tiers]


def substance_cells(rows: KnowledgeRows) -> list[list[str]]:
    return [
        [name, ", ".join(concentrations) or "—", ", ".join(equivalents) or "—"]
        for name, concentrations, equivalents in rows.substances
    ]
