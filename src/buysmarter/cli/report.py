"""Markdown rendering of a recommendation, shared by ``--save`` (and mirrors the
rich console output). Pure string building — no I/O, no rich, no network.

The console renderer in ``cli.main`` and this markdown renderer show the same
content (parsed request, best pick + axes, alternatives, top candidates, used
listings); this is the single source of the report body written to disk.
"""

import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from buysmarter.ranking.selection import is_barred
from buysmarter.services.matching import UsedListing
from buysmarter.services.recommend import NoSubstanceMatch, RecommendResult

_ROLE_LABELS = {
    "best_value": "Best value",
    "best_budget": "Best budget",
    "best_premium": "Best premium",
}
_REPORTS_DIR = Path.home() / ".local" / "share" / "buysmarter" / "reports"

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
}


def format_rejected_reasons(reasons: dict[str, int]) -> str:
    """Compact "label ×N, label ×N" breakdown, Russian-labeled where known,
    ordered by count descending (ties keep ``Counter``'s insertion order)."""
    return ", ".join(
        f"{_REASON_LABELS_RU.get(reason, reason)} ×{n}"
        for reason, n in Counter(reasons).most_common()
    )


def _fmt_price(price: int) -> str:
    return f"{price:,} ₸"


def _md_link(title: str, url: str | None) -> str:
    """Markdown ``[title](url)`` link, or the plain title when there's no URL."""
    return f"[{title}]({url})" if url else title


def _constraints_md(result: RecommendResult) -> list[str]:
    c = result.constraints
    city_note = " (по умолчанию)" if result.city_defaulted else ""
    lines = [
        "## Parsed request",
        "",
        f"- **Category:** {c.category}",
        f"- **City:** {c.city or '-'}{city_note}",
        f"- **Budget:** {_fmt_price(c.budget_max) if c.budget_max else 'без лимита'}",
    ]
    if c.required_features:
        lines.append(f"- **Required:** {', '.join(c.required_features)}")
    if c.excluded_brands:
        lines.append(f"- **Excluded:** {', '.join(c.excluded_brands)}")
    return lines


def _best_md(result: RecommendResult) -> list[str]:
    best = result.recommendation.best
    cand = best.candidate
    # M4: `best` is barred ONLY via assemble_recommendation's all-barred
    # fallback (nothing trustworthy left to prefer) — must be visible.
    barred = is_barred(best)
    title = f"⚠ {cand.title}" if barred else cand.title
    lines = [
        "",
        "## Best pick",
        "",
        f"### 🥇 {title}  [{cand.source}]",
        "",
        f"- **Score:** {best.total_score:.0f}/100",
        f"- **Confidence:** {best.confidence:.0%}",
        f"- **Price:** {_fmt_price(cand.price)}",
    ]
    if barred:
        lines.append("- **⚠ все кандидаты под подозрением — проверьте вручную**")
    if best.review_penalty > 0:
        lines.append(f"- **Penalty:** отзывы (−{best.review_penalty:.0f})")
    if cand.best_seller:
        lines.append(f"- **Продавец:** {cand.best_seller}")
    if cand.url:
        lines.append(f"- **URL:** {cand.url}")
    lines += ["", "| Axis | Score | Why |", "| --- | ---: | --- |"]
    for axis in best.axes:
        lines.append(
            f"| {axis.name} | {axis.score:.1f}/{axis.max_score:.0f} | {axis.explanation} |"
        )
    if best.pros:
        lines += ["", "**+** " + "  **+** ".join(best.pros)]
    if best.cons:
        lines += ["", "**-** " + "  **-** ".join(best.cons)]
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
        "## Alternatives",
        "",
        "| Role | Source | Product | Price | Score |",
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
            f"| {_ROLE_LABELS.get(role, role)} | {c.source} | {_md_link(c.title, c.url)} | "
            f"{_fmt_price(c.price)} | {scored.total_score:.0f} |"
        )
    return lines


def _tier_cell(c) -> str:
    """Tier column: a mimic candidate flags the counterfeit signal instead of
    the (usually empty) tier — "-" alone would hide the more useful fact."""
    if c.mimic_brand is not None:
        return f"подделка под {c.mimic_brand}?"
    return c.brand_tier or "-"


def _top_md(result: RecommendResult, top: int) -> list[str]:
    ranked = result.recommendation.ranked[:top]
    if not ranked:
        return []
    lines = [
        "",
        f"## Top candidates (top {len(ranked)})",
        "",
        "| # | Source | Product | Price | Score | Tier |",
        "| ---: | --- | --- | ---: | ---: | --- |",
    ]
    for i, scored in enumerate(ranked, start=1):
        c = scored.candidate
        # See cli.main._render_top: a barred candidate (suspicious price,
        # dup-price-gap, mimicry, review-summary counterfeit) stays in the
        # table but gets a visible "⚠" marker — it can never win any role.
        title_text = f"⚠ {c.title}" if is_barred(scored) else c.title
        lines.append(
            f"| {i} | {c.source} | {_md_link(title_text, c.url)} | {_fmt_price(c.price)} | "
            f"{scored.total_score:.0f} | {_tier_cell(c)} |"
        )
    return lines


def _used_row(listing: UsedListing) -> str:
    title = listing.title
    if listing.canonical:
        title = f"{title} → {listing.canonical}"
    saving_cell = "—" if listing.standalone else f"-{_fmt_price(listing.saving)}"
    return (
        f"| {_md_link(title, listing.url)} | {_fmt_price(listing.price)} | "
        f"{saving_cell} | {listing.city or '-'} | {listing.con} |"
    )


def _used_md(result: RecommendResult) -> list[str]:
    used = result.used_listings
    if not used:
        return []
    lines = [
        "",
        "## Б/у на OLX",
        "",
        "| Listing | Price | Saving vs new | City | Con |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    lines += [_used_row(listing) for listing in used]
    return lines


def render_markdown(result: RecommendResult, top: int = 8) -> str:
    """Render the whole recommendation as a markdown report string."""
    parts = ["# buy-smarter recommendation", ""]
    parts += _constraints_md(result)
    parts += _best_md(result)
    parts += _review_summary_md(result)
    parts += _alternatives_md(result)
    parts += _top_md(result, top)
    parts += _used_md(result)
    rejected = result.recommendation.rejected_count
    if rejected:
        parts += ["", f"_{rejected} candidate(s) rejected by hard filters._"]
        reasons = result.recommendation.rejected_reasons
        if reasons:
            parts += [f"_Отсеяно: {format_rejected_reasons(reasons)}_"]
    return "\n".join(parts) + "\n"


def render_no_substance_markdown(exc: NoSubstanceMatch) -> str:
    """Markdown for the honest empty-pool case (mirrors the CLI console output)."""
    lines = [
        "# buy-smarter recommendation",
        "",
        exc.message(),
    ]
    if exc.alternatives:
        lines += ["", "| Product | Price | Rating |", "| --- | ---: | ---: |"]
        for cand in exc.alternatives:
            rating = f"{cand.rating:.1f}" if cand.rating is not None else "-"
            lines.append(
                f"| {_md_link(cand.title, cand.url)} | {_fmt_price(cand.price)} | {rating} |"
            )
    return "\n".join(lines) + "\n"


def _slug(text: str) -> str:
    """URL/file-safe slug from a query (letters/digits → '-', trimmed)."""
    slug = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-")
    return slug[:40] or "report"


def default_report_path(query: str) -> Path:
    """``~/.local/share/buysmarter/reports/YYYYMMDD-HHMMSS-<query-slug>.md`` for now."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return _REPORTS_DIR / f"{stamp}-{_slug(query)}.md"


def save_report(result: RecommendResult, query: str, top: int, path: str | None) -> Path:
    """Write the markdown report; return the path written. Creates parent dirs."""
    target = Path(path) if path else default_report_path(query)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(result, top), encoding="utf-8")
    return target
