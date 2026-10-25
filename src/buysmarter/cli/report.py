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

from buysmarter.capabilities.provenance import assessed_entries, assessed_line
from buysmarter.capabilities.used_only import USED
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
    used_con,
)
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.ingredients import IngredientBook
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.ranking.models import Candidate, QueryConstraints
from buysmarter.ranking.selection import distinct_variants, is_barred
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


def no_new_pick_text(reasons: dict[str, int], has_used: bool) -> str:
    """What to say when no new product passed: why, and whether OLX has something."""
    head = f"Новых предложений нет: {format_rejected_reasons(reasons)}."
    return f"{head} На OLX нашлось:" if has_used else f"{head} На OLX тоже ничего."


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


def _constraints_md(result: RecommendResult) -> list[str]:
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
    notes = (
        *request_notes(result),
        assessed_note(result),
        skipped_llm_note(result),
        unavailable_sources_note(result),
        *failed_source_notes(result),
        ingredients_unchecked_note(result),
    )
    for note in notes:
        if note:
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


def _review_summary_md(result: RecommendResult) -> list[str]:
    summary = result.review_summary
    if summary is None:
        return []
    lines = ["", "## Отзывы кратко", ""]
    lines += [f"- ⚠ {f}" for f in summary.red_flags]
    lines += [f"- ✓ {p}" for p in summary.pros]
    lines += [f"- ✗ {c}" for c in summary.cons]
    lines += [f"- «{c}»" for c in summary.common_complaints]
    if summary.verdict_line:
        lines += ["", f"**{summary.verdict_line}**"]
    return lines if len(lines) > 3 else []


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


def _tier_cell(c: Candidate) -> str:
    """Tier column: a mimic candidate flags the counterfeit signal instead of
    the (usually empty) tier — "-" alone would hide the more useful fact."""
    if c.mimic_brand is not None:
        return f"подделка под {c.mimic_brand}?"
    return c.brand_tier or "-"


def _top_md(result: RecommendResult, top: int) -> list[str]:
    ranked = distinct_variants(result.recommendation.ranked, top)
    if not ranked:
        return []
    lines = [
        "",
        f"## Лучшие предложения (топ {len(ranked)})",
        "",
        "| # | Маркетплейс | Товар | Цена | Баллы | Бренд |",
        "| ---: | --- | --- | ---: | ---: | --- |",
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
            f"{scored.total_score:.0f} | {_tier_cell(c)} |"
        )
    return lines


USED_HEADERS = ["Объявление", "Цена", "Дешевле нового на", "Сравнили с", "Город", "Минус"]


def used_cells(listing: UsedListing, brands: set[str]) -> list[str]:
    """An OLX ad as the app and the console show it, one cell per ``USED_HEADERS`` column."""
    title = f"{listing.title} → {listing.canonical}" if listing.canonical else listing.title
    standalone = listing.standalone or not listing.matched_title
    saving = "—" if listing.standalone else kzt(listing.saving)
    compared = "—" if standalone else clean_title(listing.matched_title or "")
    con = used_con(listing, brands)
    return [title, kzt(listing.price), saving, compared, listing.city or "—", con]


def _used_row(listing: UsedListing, brands: set[str]) -> str:
    title = listing.title
    if listing.canonical:
        title = f"{title} → {listing.canonical}"
    saving_cell = "—" if listing.standalone else f"-{_fmt_price(listing.saving)}"
    return (
        f"| {_md_link(title, listing.url)} | {_fmt_price(listing.price)} | "
        f"{saving_cell} | {listing.city or '-'} | {used_con(listing, brands)} |"
    )


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
        "| Объявление | Цена | Дешевле нового на | Город | Минус |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    lines += [_used_row(listing, brands) for listing in used]
    return lines


def render_markdown(result: RecommendResult, top: int = 8) -> str:
    """Render the whole recommendation as a markdown report string."""
    parts = [REPORT_TITLE, ""]
    if result.restricted_note:
        parts += [f"> ⚠ {result.restricted_note}", ""]
    parts += _constraints_md(result)
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
        lines += ["", "| Товар | Цена | Рейтинг |", "| --- | ---: | ---: |"]
        for cand in exc.alternatives:
            rating = f"{cand.rating:.1f}" if cand.rating is not None else "-"
            lines.append(
                f"| {_md_link(clean_title(cand.title), cand.url)} | {_fmt_price(cand.price)} "
                f"| {rating} |"
            )
    return "\n".join(lines) + "\n"


def render_no_new_pick_markdown(exc: NoNewPick) -> str:
    """Markdown for "no new product passed": the reasons and the OLX ads within the budget."""
    text = no_new_pick_text(exc.rejected_reasons, bool(exc.used_listings))
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


def saved_reports() -> list[Path]:
    """Every report ``save_report`` wrote to the default folder, newest first."""
    if not _REPORTS_DIR.is_dir():
        return []
    return sorted(_REPORTS_DIR.glob("*.md"), key=lambda p: p.name, reverse=True)


def save_report(result: RecommendResult, query: str, top: int, path: str | None) -> Path:
    """Write the markdown report; return the path written. Creates parent dirs."""
    target = Path(path) if path else default_report_path(query)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(result, top), encoding="utf-8")
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
