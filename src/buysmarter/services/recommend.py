"""Recommendation orchestration: query → multi-source → normalize → rank.

Ties the vertical slice together (docs/PLAN.md "Universal pipeline"): parse the
request, search every selected marketplace concurrently, normalize to ranking
candidates, cross-source dedupe, best-effort persist, then rank. Kaspi + Ozon
feed the main ranking pool; OLX used listings are matched against the winners
and surfaced as a separate advisory "Б/у на OLX" section (docs/INIT_DATA.md
"Used Market" — the "б/у может быть лучше" differentiator). DB and LLM are both
optional — the CLI must work keyless and without Postgres, and one source
failing (e.g. an Ozon browser hiccup) must never kill the run.
"""

import asyncio
import functools
import inspect
import logging
import re
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast

from market_sources.base import ProductSource, RawProduct, RawReview, RawReviewStats
from market_sources.kaspi.client import KaspiClient
from market_sources.kaspi.source import KaspiSource
from market_sources.olx.source import OlxSource
from market_sources.ozon.browser import OzonBrowser
from market_sources.ozon.source import OzonSource
from pydantic import BaseModel, Field

from buysmarter import telemetry
from buysmarter.config import get_settings
from buysmarter.db.base import async_session
from buysmarter.db.repo import (
    add_offers,
    add_price_snapshot,
    find_products,
    get_preference,
    get_product,
    get_tracked_categories,
    latest_snapshot,
    list_snapshots,
    price_history,
    review_count_history,
    track_category,
    upsert_product,
)
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.ingredients import IngredientBook, composition_text
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.knowledge.store import (
    COMPOSITION_DRIVEN,
    resolve_books,
    resolve_composition,
)
from buysmarter.llm.adapter import LlmConfigError, LlmError
from buysmarter.llm.ingredients import extract_ingredients
from buysmarter.llm.prompts import parse_query, parse_query_naive
from buysmarter.llm.relevance import accessory_reason, filter_relevant
from buysmarter.llm.review_summary import RequirementCheck, ReviewSummary, summarize_reviews
from buysmarter.normalize.candidate import to_candidate
from buysmarter.ranking.engine import (
    NO_SUBSTANCE_REASON,
    apply_defect_penalty,
    apply_hard_filters,
    apply_requirement_checks,
    apply_review_trust,
    apply_summary_penalty,
    assemble_recommendation,
    feature_present,
    rank,
)
from buysmarter.ranking.models import (
    Candidate,
    PriceHistory,
    QueryConstraints,
    Recommendation,
    ScoredCandidate,
)
from buysmarter.ranking.review_requirements import naive_requirement_checks
from buysmarter.ranking.review_trust import count_defect_complaints, review_trust
from buysmarter.ranking.used_defects import is_for_parts
from buysmarter.ranking.velocity import review_velocity
from buysmarter.services.matching import (
    UsedListing,
    ad_text,
    apply_cross_notes,
    dedupe_cross_source,
    is_jargon_only,
    matches_category_or_stripped,
    strip_latin_tokens,
    title_matches_category,
    used_market,
)
from buysmarter.services.phone_specs import enrich_with_kimovil, is_smartphone_category

logger = logging.getLogger(__name__)

# When a BrandBook exists, brand-targeted searches widen the pool considerably
# (main search + up to ~6 brand searches), so the top-N budgets are bumped a
# little to give the new brand axis real candidates without going wild.
_MAX_CARDS = 16  # Kaspi search hits to consider
_OFFERS_FOR = 8  # fetch Kaspi seller offers only for the top N (be gentle)
_KASPI_DETAIL_CONCURRENCY = 4  # parallel per-product specs+offers fetches
_BRAND_SEARCH_TOP_N = 2  # top cards per brand+alias search that actually match that brand
_MAX_BRAND_SEARCHES = 30  # total brand+alias searches per run (canonicals first)
_MAX_TARGETED_CARDS = 40  # total substance+brand targeted hits kept, uncapped by _MAX_CARDS
_BEST_VALUE_C_TIER_BRAND = "Xiaomi"  # always searched even though it's tier C

# Ozon uses a real browser (slow navigation) so the budgets are tighter: take
# the top few cards, and only pay for spec pages on the very top handful.
_OZON_MAX_CARDS = 8
_OZON_SPECS_FOR = 4

# OLX is classifieds (one ad == one offer); used listings are interleaved among
# many new ones across the whole ~50-result pool, and OlxSource serves specs
# from its search cache (no per-listing HTTP), so we scan the full pool.
_OLX_MAX_CARDS = 60
_OLX_HEAD_NOUN_MIN_LEN = 4  # shortest token treated as a searchable head noun

ALL_SOURCES = ("kaspi", "ozon", "olx")


def select_sources(names: Iterable[str], allowed: Sequence[str] = ALL_SOURCES) -> list[str]:
    """Normalize marketplace names, dedupe, keep order; an empty selection means all ``allowed``.

    Raises ``ValueError`` naming every unknown or disallowed source (CLI, MCP and API share it).
    """
    selected = list(dict.fromkeys(n.strip().lower() for n in names if n.strip()))
    rejected = [n for n in selected if n not in allowed]
    if rejected:
        raise ValueError(f"unknown source(s) {', '.join(rejected)}; use {', '.join(allowed)}")
    return selected or list(allowed)


# City name → Kaspi cityId. Unknown → None (KaspiClient defaults to Almaty).
_CITY_IDS = {
    "almaty": "750000000",
    "astana": "710000000",
    "алматы": "750000000",
    "астана": "710000000",
}


@contextmanager
def _stage(name: str):
    """Log one INFO line with the elapsed wall time of a pipeline stage."""
    start = time.perf_counter()
    try:
        yield
    finally:
        logger.info("stage %s took %.2fs", name, time.perf_counter() - start)


def _city_id(city: str | None) -> str | None:
    """Kaspi cityId for a city name, case/whitespace-insensitive."""
    return _CITY_IDS.get((city or "").strip().lower())


# Last-resort city when neither the query, --city, nor a stored preference
# yields one (docs/INIT_DATA.md, most users are in Almaty).
_DEFAULT_CITY = "Алматы"
_CITY_PREFERENCE_KEY = "city"


class RecommendResult(BaseModel):
    """Pipeline output: the ranking, the constraints used, and used listings."""

    recommendation: Recommendation
    constraints: QueryConstraints
    used_listings: list[UsedListing] = Field(default_factory=list)
    city_defaulted: bool = False
    review_summary: ReviewSummary | None = None


async def _parse(query: str, use_llm: bool) -> QueryConstraints:
    if not use_llm:
        return parse_query_naive(query)
    try:
        return await parse_query(query)
    except (LlmError, LlmConfigError) as exc:
        logger.warning(
            "LLM query parse failed (%s); is the `claude` CLI installed and "
            "authenticated? Falling back to naive keyword parsing",
            exc,
        )
        return parse_query_naive(query)


async def parse_request(query: str, use_llm: bool) -> QueryConstraints:
    """Public parse step so the CLI can inspect ``budget_max`` before running
    the pipeline (to decide whether to prompt), then feed the same parse back
    into ``recommend`` via ``parsed=`` instead of parsing the query twice.
    """
    return await _parse(query, use_llm)


async def _city_preference() -> str | None:
    """Best-effort lookup of a stored city preference; ``None`` on any failure."""
    try:
        async with async_session() as session:
            value = await get_preference(session, _CITY_PREFERENCE_KEY)
    except Exception as exc:  # noqa: BLE001 - a dead DB must not block a default
        logger.warning("City preference lookup failed (DB unavailable?): %r", exc)
        return None
    return value if isinstance(value, str) and value else None


async def _resolve_city(parsed_city: str | None, override: str | None) -> tuple[str, bool]:
    """City precedence: --city override > query > stored preference > "Алматы".

    Returns ``(city, defaulted)`` where ``defaulted`` is True whenever neither
    the query nor ``--city`` named a city, so the CLI can flag it as such.
    """
    if override:
        return override, False
    if parsed_city:
        return parsed_city, False
    pref = await _city_preference()
    return pref or _DEFAULT_CITY, True


def _brand_search_targets(book: BrandBook) -> list[str]:
    """Canonical brands worth a targeted search: tier A, then tier B, then the
    best-value-C carve-out — in THAT order, so a downstream cap
    (``_MAX_BRAND_SEARCHES``, ``_MAX_TARGETED_CARDS``) trims the least
    important brand first instead of an arbitrary tier-A casualty from
    ``dict`` iteration order (docs/PLAN.md "Ranking-фиксы")."""
    tier_a = [brand for brand, tier in book.tiers.items() if tier == "A"]
    tier_b = [brand for brand, tier in book.tiers.items() if tier == "B"]
    targets = tier_a + tier_b
    if _BEST_VALUE_C_TIER_BRAND in book.tiers and _BEST_VALUE_C_TIER_BRAND not in targets:
        targets.append(_BEST_VALUE_C_TIER_BRAND)
    return targets


_LATIN_ALIAS_RE = re.compile(r"^[a-z0-9 .\-]+$", re.IGNORECASE)
_MIN_SUB_BRAND_ALIAS_LEN = 3


def _normalize_brand_text(text: str) -> str:
    """Lowercase, alnum-only — for formatting-respelling-equality comparisons."""
    return re.sub(r"[^0-9a-z]", "", text.lower())


def _is_searchable_sub_brand_alias(alias: str, canonical: str) -> bool:
    """A sub-brand alias worth its OWN search term (Redmi->Xiaomi, CMF->Nothing,
    Soundcore->Anker Soundcore) vs. a mere formatting respelling of the SAME
    name ("OralB"/"Oral B"->Oral-B, "BWell"/"B Well"->B.Well) that the
    canonical's own search already covers.

    "not a substring of canonical and vice versa" (spec wording) is read as
    MUTUAL substring, which for two finite strings holds only at exact
    equality — a strict one-directional substring check would also reject a
    genuine sub-brand whose name happens to end in the parent's own name
    (e.g. "Soundcore" is literally a substring of "Anker Soundcore"), which
    contradicts that same worked example.
    """
    if not _LATIN_ALIAS_RE.match(alias):
        return False
    normalized_alias = _normalize_brand_text(alias)
    if len(normalized_alias) < _MIN_SUB_BRAND_ALIAS_LEN:
        return False
    return normalized_alias != _normalize_brand_text(canonical)


def _brand_search_plan(book: BrandBook) -> list[tuple[str, str]]:
    """(search term, canonical brand ``resolve(term)`` will actually return),
    tier-A canonicals first, then tier-B/best-value-C, then their sub-brand
    aliases (each alias sorted right after its own canonical, so a downstream
    cap trims by tier, not by dict-iteration luck) — deduped by normalized
    term, capped at ``_MAX_BRAND_SEARCHES`` total (applied here, BEFORE any
    search is issued).

    The plan's second element is what ``book.resolve(term)`` itself returns
    for that exact term, NOT the tiers-dict key it came from: aliases win
    priority in ``resolve``, so a term that is simultaneously a canonical
    AND (shadowed by) an alias of a DIFFERENT brand — e.g. "Honor" is a
    tier-A canonical here but also a known alias of "Huawei" — resolves to
    "Huawei", not "Honor". Storing the naive tiers-dict key would make the
    per-card check in ``_brand_targeted_products`` (``resolve(title)[0] ==
    canonical``) fail for EVERY card the search returns, since real cards
    resolve to "Huawei" too — silently zeroing out a tier-A brand's recall.
    """
    canonicals = _brand_search_targets(book)  # tier-A, then tier-B/best-value-C
    seen_terms: set[str] = set()
    plan: list[tuple[str, str]] = []

    def _add(term: str) -> None:
        key = _normalize_brand_text(term)
        if key in seen_terms:
            return
        seen_terms.add(key)
        resolved_canonical, _tier = book.resolve(term)
        plan.append((term, resolved_canonical or term))

    for brand in canonicals:
        _add(brand)
    for canonical in canonicals:
        for alias, alias_canonical in book.aliases.items():
            if alias_canonical != canonical:
                continue
            if _is_searchable_sub_brand_alias(alias, canonical):
                _add(alias)
    return plan[:_MAX_BRAND_SEARCHES]


async def _brand_targeted_products(
    source: ProductSource, category: str, city_id: str | None, book: BrandBook
) -> list[RawProduct]:
    """Search "<term> <category>" for every known brand + sub-brand alias.

    A plain category search on Kaspi is dominated by noname listings, so known
    brands (Waterpik, Xiaomi, B.Well, ...) never make the candidate pool. This
    runs one extra search per targeted brand/alias term (``_brand_search_plan``)
    and filters each result down to cards whose title actually resolves to
    that term's CANONICAL brand *and* matches the category, dropping noise
    (unrelated products from the same brand). The category itself is the
    latin-jargon-stripped variant when one exists ("TWS наушники" ->
    "наушники") — Kaspi ANDs terms and rarely carries the jargon in titles;
    ``book`` also protects any latin token that's itself a known brand the
    user typed (e.g. "Apple") from being stripped.
    """
    stripped = strip_latin_tokens(category, book)
    query_category = stripped if stripped is not None else category
    # A jargon-only category (e.g. bare "TWS", all-latin) has no real
    # category word to check the title against at all — requiring one would
    # reject a genuinely brand-confirmed card just because real listings
    # never carry the jargon acronym. The brand match alone is the evidence
    # in that case.
    jargon_only = is_jargon_only(category)
    found: list[RawProduct] = []
    for term, canonical in _brand_search_plan(book):
        try:
            cards = await source.search(f"{term} {query_category}", city=city_id)
        except Exception as exc:  # noqa: BLE001 - one bad brand search shouldn't kill recall
            logger.warning("Brand search failed for %r: %r", term, exc)
            continue
        matched = [
            card
            for card in cards
            if book.resolve(card.title)[0] == canonical
            and (jargon_only or title_matches_category(card.title, category))
        ]
        found.extend(matched[:_BRAND_SEARCH_TOP_N])
    return found


# Substance-targeted recall (docs/PLAN.md "Composition-driven категории"): a
# plain "крем с адапаленом" search on Kaspi is dominated by moisturizers "for use
# during acne treatment" that contain no adapalene, while the actual adapalene
# products hide behind their brand names (Клензит/Дифферин/Адаклин). Mirror the
# brand-targeted recall: search the substance itself and each equivalence-group
# product name, keep only cards whose title confirms the substance.
_MAX_SUBSTANCE_SEARCHES = 5  # total extra searches across all required substances
_SUBSTANCE_SEARCH_TOP_N = 3  # top cards per substance search kept


def _substance_search_plan(
    book: IngredientBook, required_substances: list[str]
) -> list[tuple[str, list[str]]]:
    """(query, title-needles) pairs for substance recall, capped and deduped by query."""
    plan: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for canonical in required_substances:
        needles = book.title_needles_for(canonical)
        for term in book.search_terms_for(canonical):
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            plan.append((term, needles))
    return plan[:_MAX_SUBSTANCE_SEARCHES]


async def _substance_targeted_products(
    source: ProductSource, plan: list[tuple[str, list[str]]], city_id: str | None
) -> list[RawProduct]:
    """Search each substance/equivalent name; keep cards whose title confirms it.

    Title confirmation is recall-stage relevance only; the состав-based ingredient
    extraction + hard filter downstream is the authoritative absence check.
    """
    found: list[RawProduct] = []
    for query, needles in plan:
        try:
            cards = await source.search(query, city=city_id)
        except Exception as exc:  # noqa: BLE001 - one bad substance search shouldn't kill recall
            logger.warning("Substance search failed for %r: %r", query, exc)
            continue
        lowered_needles = [n.lower() for n in needles]
        matched = [card for card in cards if any(n in card.title.lower() for n in lowered_needles)]
        found.extend(matched[:_SUBSTANCE_SEARCH_TOP_N])
    return found


def _substance_confirmed_ids(
    candidates: list[Candidate], book: IngredientBook | None, required_substances: list[str]
) -> set[str]:
    """source_product_ids whose title names a required substance or its equivalent.

    These are the recall hits the category-relevance filter must not evict (an
    adapalene gel is the answer to "крем с адапаленом", form factor aside).
    """
    if book is None or not required_substances:
        return set()
    needles = [
        n.lower() for canonical in required_substances for n in book.title_needles_for(canonical)
    ]
    return {c.source_product_id for c in candidates if any(n in c.title.lower() for n in needles)}


def _dedupe_products(products: list[RawProduct]) -> list[RawProduct]:
    """Drop later duplicates by ``product_id``, keeping first-seen order."""
    seen: set[str] = set()
    deduped: list[RawProduct] = []
    for product in products:
        if product.product_id in seen:
            continue
        seen.add(product.product_id)
        deduped.append(product)
    return deduped


async def _safe_specs(source: ProductSource, product_id: str) -> tuple[dict, bool]:
    """Best-effort Kaspi specs fetch — one bad product id must not skip the whole source.

    Fashion/apparel categories can still 404 on a per-product basis even with a
    normalized numeric id (some variant ids simply have no content page); the
    source-level try/except only guards the search call, so each item's specs
    fetch needs its own guard too. Logged at INFO (file-only per logsetup.py) —
    a per-item spec miss is expected data absence, not something worth a
    console WARNING. Returns ``(specs, ok)`` so the caller can aggregate
    failures into one summary line instead of one WARNING per candidate.
    """
    try:
        return await source.specs(product_id), True
    except Exception as exc:  # noqa: BLE001 - one bad product must not kill the source
        name = str(getattr(source, "name", "source")).capitalize()
        logger.info("%s specs fetch failed for product %s: %r", name, product_id, exc)
        return {}, False


async def _safe_offers(source: ProductSource, product_id: str) -> list:
    """Best-effort Kaspi offers fetch — one bad product id must not skip the whole source.

    Logged at INFO, same reasoning as ``_safe_specs`` — a per-item failure is
    expected noise, not console-worthy on its own.
    """
    try:
        return await source.offers(product_id)
    except Exception as exc:  # noqa: BLE001 - one bad product must not kill the source
        logger.info("Kaspi offers fetch failed for product %s: %r", product_id, exc)
        return []


async def _collect_kaspi(
    source: ProductSource,
    category: str,
    city_id: str | None,
    book: BrandBook | None,
    substance_plan: list[tuple[str, list[str]]] | None = None,
) -> tuple[list[Candidate], list[tuple]]:
    """Kaspi: brand/substance-targeted + main search, specs for all, offers for top N."""
    stripped = strip_latin_tokens(category, book)
    products = await source.search(category, city=city_id)
    search_count = 1
    if stripped is not None:
        # Full-text search ANDs terms; a jargon token ("TWS") that never
        # appears in listing titles can zero out the main search entirely
        # even though matching products exist under a plainer query.
        products = _dedupe_products(products + await source.search(stripped, city=city_id))
        search_count += 1
    # Targeted hits go FIRST and are EXEMPT from `_MAX_CARDS`: the main
    # category search on Kaspi is dominated by noname/off-target listings, so
    # truncating targeted hits by the same budget as the noisy main pool would
    # throw away the very correct-substance / known-brand candidates recall
    # exists to find. Substance hits lead brand hits: for a composition-driven
    # category a related BrandBook (e.g. крем-от-акне's Vichy/CeraVe...) would
    # otherwise flood the cap with moisturizer brands and push the actual
    # active-substance products (Клензит...) out of the pool. Brand hits
    # themselves accumulate in ``_brand_search_plan``'s tier-A-first order, so
    # the ``_MAX_TARGETED_CARDS`` cut below drops the LEAST important brand's
    # hits first too, not an arbitrary one.
    targeted: list[RawProduct] = []
    if substance_plan:
        targeted += await _substance_targeted_products(source, substance_plan, city_id)
        search_count += len(substance_plan)
    if book is not None:
        targeted += await _brand_targeted_products(source, category, city_id, book)
        search_count += len(_brand_search_plan(book))
    targeted = _dedupe_products(targeted)[:_MAX_TARGETED_CARDS]
    targeted_ids = {p.product_id for p in targeted}
    rest = [p for p in products if p.product_id not in targeted_ids]
    top = targeted + rest[:_MAX_CARDS]
    logger.info(
        "kaspi: %d searches issued (main+stripped+substance+brand), %d candidates gathered",
        search_count,
        len(top),
    )
    # Offer fetch (a real HTTP call each) is split evenly across the two
    # sub-pools instead of "first _OFFERS_FOR of the combined list": targeted
    # hits are always listed first, so an index-only cutoff always gave
    # targeted candidates their true cheapest-offer price while every
    # main-pool card kept the (usually higher) search-card price — a
    # systematic bias in the pool-relative price axis (docs/PLAN.md
    # "Ranking-фиксы").
    half = _OFFERS_FOR // 2
    offer_eligible_ids = {p.product_id for p in targeted[:half]} | {
        p.product_id for p in rest[:half]
    }
    semaphore = asyncio.Semaphore(_KASPI_DETAIL_CONCURRENCY)

    async def fetch_details(product: RawProduct) -> tuple[dict, bool, list]:
        async with semaphore:
            if product.specs_available:
                specs, ok = await _safe_specs(source, product.product_id)
            else:
                # Fashion/apparel variant card (stripped variant-letter id) — the
                # content API has no page for it, so skip the guaranteed-404 HTTP
                # call entirely rather than eat its latency for nothing.
                specs, ok = {}, False
            offers = (
                await _safe_offers(source, product.product_id)
                if product.product_id in offer_eligible_ids
                else []
            )
            return specs, ok, offers

    # gather preserves input order, so candidates/rows keep the `top` order.
    details = await asyncio.gather(*(fetch_details(p) for p in top))
    candidates: list[Candidate] = []
    rows: list[tuple] = []
    unavailable = 0
    for product, (specs, ok, offers) in zip(top, details, strict=True):
        if not ok:
            unavailable += 1
        candidates.append(to_candidate(product, specs, offers, book))
        rows.append((product, specs, offers))
    if unavailable:
        logger.warning("kaspi: specs unavailable for %d of %d candidates", unavailable, len(top))
    return candidates, rows


async def _collect_ozon(
    source: ProductSource, category: str, book: BrandBook | None
) -> tuple[list[Candidate], list[tuple]]:
    """Ozon: main search only; specs (a browser nav each) for the very top few."""
    products = await source.search(category)
    top = products[:_OZON_MAX_CARDS]
    candidates: list[Candidate] = []
    rows: list[tuple] = []
    for index, product in enumerate(top):
        # Per-item guard: one browser hiccup must not discard the whole Ozon pool.
        specs: dict = {}
        if index < _OZON_SPECS_FOR:
            specs, _ok = await _safe_specs(source, product.product_id)
        candidates.append(to_candidate(product, specs, [], book))
        rows.append((product, specs, []))
    return candidates, rows


async def _olx_call(source: ProductSource, query: str, budget: int | None) -> list[RawProduct]:
    """One OLX search, passing ``budget`` only to adapters whose search accepts it.

    The real ``OlxSource.search`` takes a ``budget`` (→ ``filter_float_price:to``);
    injected test doubles may not, so we pass it only when the signature has it.
    """
    if budget is not None and "budget" in inspect.signature(source.search).parameters:
        return await source.search(query, budget=budget)  # type: ignore[call-arg]
    return await source.search(query)


def _head_noun(category: str, book: BrandBook | None = None) -> str | None:
    """Last content token of a category (RU/EN head noun), or ``None`` if
    searching it separately would just repeat the exact same query.

    OLX classifieds full-text search ANDs terms, so a multi-word category like
    "портативный ирригатор" returns far fewer ads than the bare noun
    "ирригатор" — we broaden recall by also searching the head noun. Short
    jargon tokens (len < ``_OLX_HEAD_NOUN_MIN_LEN``, e.g. "TWS") are dropped
    before picking the head noun, same reasoning as ``strip_latin_tokens``;
    a SINGLE surviving token still broadens recall whenever it differs from
    the full (lowercased) category string — e.g. "TWS наушники" drops to one
    token, "наушники", which is strictly narrower than the two-word original.

    ``book``, when given, ALSO drops a token that is itself a known brand/
    model name (e.g. "Sony" in "беспроводные наушники Sony", "Dyson" in "фен
    Dyson Supersonic") — searching a bare brand name on OLX with no category
    filter downstream floods the pool with that brand's unrelated catalog
    (docs/PLAN.md "Ranking-фиксы"); the previous non-brand token is used
    instead, falling out naturally once brand tokens are filtered before
    picking the tail.
    """
    lowered_category = category.lower().strip()
    tokens = [
        t for t in re.findall(r"[^\W\d_]+", lowered_category) if len(t) >= _OLX_HEAD_NOUN_MIN_LEN
    ]
    if book is not None:
        tokens = [t for t in tokens if book.resolve(t)[0] is None]
    if not tokens:
        return None
    noun = tokens[-1]
    if len(tokens) >= 2:
        return noun
    return noun if noun != lowered_category else None


async def _olx_search(
    source: ProductSource, category: str, budget: int | None, book: BrandBook | None = None
) -> list[RawProduct]:
    """OLX search broadened by the latin-stripped variant + head noun, deduped.

    The full-category hits come first (most relevant); the latin-jargon-stripped
    variant ("TWS наушники" -> "наушники") and head-noun hits widen the pool so
    branded used deals hidden behind a narrow/jargon-heavy query surface.
    """
    products = await _olx_call(source, category, budget)
    stripped = strip_latin_tokens(category, book)
    if stripped is not None:
        products = _dedupe_products(products + await _olx_call(source, stripped, budget))
    noun = _head_noun(category, book)
    if noun is not None:
        products = _dedupe_products(products + await _olx_call(source, noun, budget))
    return products


def _is_used(condition: str | None) -> bool:
    """True when an OLX listing's condition reads as used (б/у), not new."""
    if not condition:
        return False
    low = condition.lower()
    return any(kw in low for kw in ("б/у", "б.у", "бу", "used", "уценк"))


async def _collect_olx(
    source: ProductSource, category: str, book: BrandBook | None, budget: int | None
) -> list[Candidate]:
    """OLX: scan the top listings, keep only used, on-category ones as match candidates."""
    products = await _olx_search(source, category, budget, book)
    search_count = (
        1
        + (1 if strip_latin_tokens(category, book) is not None else 0)
        + (1 if _head_noun(category, book) is not None else 0)
    )
    logger.info(
        "olx: %d searches issued (main+stripped+head-noun), %d cards", search_count, len(products)
    )
    used: list[Candidate] = []
    for product in products[:_OLX_MAX_CARDS]:
        # A degenerate recall query (a brand-only head-noun search, or a
        # jargon-only category) can return cards with no real relation to the
        # category at all — this safety net rejects them (HIGH-3, docs/PLAN.md
        # "Ranking-фиксы").
        if not matches_category_or_stripped(product.title, category, book):
            continue
        # Per-item guard: one failed detail fetch must not discard every used ad.
        specs, _ok = await _safe_specs(source, product.product_id)
        if not _is_used(specs.get("condition")):
            continue
        candidate = to_candidate(product, specs, [], book)
        if is_for_parts(ad_text(candidate)):
            continue
        used.append(candidate)
    return used


def _resolve_sources(
    names: list[str],
    kaspi: ProductSource | None,
    ozon: ProductSource | None,
    olx: ProductSource | None,
) -> dict[str, ProductSource]:
    """Instantiate the selected sources, honoring any injected overrides.

    ``market_sources`` falls back to ``KASPI_PROXY`` / ``OZON_BASE_URL`` env vars
    when nothing is passed; buy-smarter's ``Settings`` stays the primary route,
    so both are wired in explicitly here.
    """
    settings = get_settings()
    factories = {
        "kaspi": lambda: kaspi or KaspiSource(KaspiClient(proxy=settings.kaspi_proxy)),
        "ozon": lambda: ozon or OzonSource(OzonBrowser(base_url=settings.ozon_base_url)),
        "olx": lambda: olx or OlxSource(),
    }
    return {name: factories[name]() for name in names if name in factories}


async def _safe(name: str, coro):
    """Await a source coroutine, swallowing failures so one source can't kill the run."""
    try:
        with _stage(f"collect[{name}]"):
            result = await coro
    except Exception as exc:  # noqa: BLE001 - one dead source must not abort the pipeline
        logger.warning("Source %r failed, skipping it: %r", name, exc, exc_info=True)
        telemetry.SOURCE_REQUESTS.add(1, {"source": name, "outcome": "error"})
        return None
    telemetry.SOURCE_REQUESTS.add(1, {"source": name, "outcome": "ok"})
    return result


def _snapshot_values(product: RawProduct, offers: list) -> tuple[int, int] | None:
    """Min price seen + offer count for a persist row, or ``None`` if no price.

    Prefers the cheapest live offer, falling back to the search-card price so a
    source that returns no offers (e.g. Ozon) still records a snapshot.
    """
    prices = [int(o.price) for o in offers if getattr(o, "price", None) and o.price > 0]
    if product.price and product.price > 0:
        prices.append(int(product.price))
    if not prices:
        return None
    return min(prices), len(offers)


async def _persist(rows: list[tuple], category: str | None = None) -> None:
    """Best-effort persistence; a dead DB must not break the CLI.

    Upserts each product, records its offers, and writes one ``PriceSnapshot``
    (min price seen + offer count) per candidate so price history accrues run
    over run. Also records ``category`` into the tracked-categories preference
    (for the ``refresh`` command). Any DB failure is swallowed.
    """
    try:
        async with async_session() as session:
            for product, specs, offers in rows:
                db_product = await upsert_product(session, product)
                if specs:
                    db_product.specs = specs
                    await session.commit()
                if offers:
                    await add_offers(session, db_product.id, offers)
                snapshot = _snapshot_values(product, offers)
                if snapshot is not None:
                    await add_price_snapshot(
                        session,
                        db_product.id,
                        snapshot[0],
                        snapshot[1],
                        review_count=product.review_count,
                        rating=product.rating,
                    )
            if category:
                await track_category(session, category)
    except Exception as exc:  # noqa: BLE001 - persistence is optional
        logger.warning("Persistence skipped (DB unavailable?): %r", exc, exc_info=True)


_HISTORY_DAYS = 30


async def _attach_price_history(candidates: list[Candidate]) -> None:
    """Best-effort: attach each candidate's 30-day price history from the DB.

    One short-lived session, sequential lookups (gentle). Without Postgres the
    whole thing is swallowed and candidates simply carry no history — the price
    axis then behaves exactly as before.
    """
    if not candidates:
        return
    now = datetime.now(UTC)
    try:
        async with async_session() as session:
            for cand in candidates:
                product = await get_product(session, cand.source, cand.source_product_id)
                if product is None:
                    continue
                hist = await price_history(session, product.id, _HISTORY_DAYS)
                if hist is not None:
                    avg, minimum, count = hist
                    cand.price_history = PriceHistory(avg=avg, min=minimum, count=count)
                if cand.review_count is not None:
                    counts = await review_count_history(session, product.id)
                    cand.review_velocity = review_velocity(counts, cand.review_count, now)
    except Exception as exc:  # noqa: BLE001 - history is an optional signal
        logger.warning("Price-history lookup skipped (DB unavailable?): %r", exc)


async def _aclose(source: ProductSource) -> None:
    """Release a source's HTTP pool / browser, if it exposes ``aclose``.

    Every ``market_sources`` adapter does (Kaspi/OLX hold a connection pool, Ozon
    a browser); the ``getattr`` guard is for injected test stubs that do not.
    """
    closer = getattr(source, "aclose", None)
    if closer is None:
        return
    try:
        await closer()
    except Exception as exc:  # noqa: BLE001 - teardown must never surface
        logger.warning("Source teardown failed: %r", exc)


async def _gather(
    active: dict[str, ProductSource],
    category: str,
    city_id: str | None,
    book: BrandBook | None,
    budget: int | None,
    substance_plan: list[tuple[str, list[str]]] | None = None,
) -> dict[str, object]:
    """Run every active source's collector concurrently, isolating failures."""
    coros: dict[str, object] = {}
    if "kaspi" in active:
        coros["kaspi"] = _collect_kaspi(active["kaspi"], category, city_id, book, substance_plan)
    if "ozon" in active:
        coros["ozon"] = _collect_ozon(active["ozon"], category, book)
    if "olx" in active:
        coros["olx"] = _collect_olx(active["olx"], category, book, budget)
    names = list(coros)
    results = await asyncio.gather(*(_safe(n, coros[n]) for n in names))
    return dict(zip(names, results, strict=True))


# Review-trust re-score / summarization only pay for reviews on the top ranked
# Kaspi candidates (Kaspi's review endpoint is cheap; Ozon's is browser-heavy).
_REVIEW_TRUST_TOP_N = 5
_REVIEW_SOURCE = "kaspi"
_REVIEW_FETCH_CONCURRENCY = 3  # parallel trust-pass review fetches

ReviewsById = dict[tuple[str, str], list[RawReview]]
StatsById = dict[tuple[str, str], RawReviewStats]


def _candidate_key(scored: ScoredCandidate) -> tuple[str, str]:
    return (scored.candidate.source, scored.candidate.source_product_id)


async def _reviews_and_stats(
    source: ProductSource, product_id: str
) -> tuple[list[RawReview], RawReviewStats | None]:
    """Reviews plus aggregate stats when the source has them (Kaspi), else no stats."""
    with_stats = getattr(source, "reviews_with_stats", None)
    if with_stats is None:
        return await source.reviews(product_id), None
    return await with_stats(product_id)


async def _fetch_top_reviews(
    source: ProductSource, recommendation: Recommendation
) -> tuple[ReviewsById, StatsById]:
    """Fetch reviews (and stats) for the top ranked Kaspi candidates, best-effort per id."""
    top = [s for s in recommendation.ranked if s.candidate.source == _REVIEW_SOURCE]
    semaphore = asyncio.Semaphore(_REVIEW_FETCH_CONCURRENCY)

    async def fetch(
        scored: ScoredCandidate,
    ) -> tuple[list[RawReview], RawReviewStats | None] | None:
        async with semaphore:
            try:
                return await _reviews_and_stats(source, scored.candidate.source_product_id)
            except Exception as exc:  # noqa: BLE001 - a review fetch must not kill the run
                logger.warning(
                    "Review fetch failed for %s: %r", scored.candidate.source_product_id, exc
                )
                return None

    selected = top[:_REVIEW_TRUST_TOP_N]
    fetched = await asyncio.gather(*(fetch(scored) for scored in selected))
    reviews_by_id: ReviewsById = {}
    stats_by_id: StatsById = {}
    for scored, result in zip(selected, fetched, strict=True):
        if result is None:
            continue
        reviews, stats = result
        reviews_by_id[_candidate_key(scored)] = reviews
        if stats is not None:
            stats_by_id[_candidate_key(scored)] = stats
    return reviews_by_id, stats_by_id


def _apply_review_trust(
    recommendation: Recommendation,
    reviews_by_id: ReviewsById,
    stats_by_id: StatsById | None = None,
) -> Recommendation:
    """Re-score low-trust and defect-complaint candidates, then re-assemble.

    Small explicit second pass: per fetched candidate, rescale the reviews axis
    by its trust multiplier (when < 1.0) and apply the defect-complaint penalty.
    Re-sorts only when something changed.
    """
    stats_by_id = stats_by_id or {}
    adjusted: dict[tuple[str, str], ScoredCandidate] = {}
    for scored in recommendation.ranked:
        key = _candidate_key(scored)
        reviews = reviews_by_id.get(key)
        if not reviews:
            continue
        trust, note = review_trust(reviews, stats_by_id.get(key))
        updated = apply_review_trust(scored, trust, note) if trust < 1.0 else scored
        # ponytail: complaint share uses the fetched sample (~9 Kaspi reviews), not stats.total.
        # Fetch more review pages at the first best pick decided by this penalty on < 20 reviews.
        updated = apply_defect_penalty(updated, count_defect_complaints(reviews), len(reviews))
        if updated is not scored:
            adjusted[key] = updated
    if not adjusted:
        return recommendation
    rescored = [adjusted.get(_candidate_key(s), s) for s in recommendation.ranked]
    return assemble_recommendation(
        rescored, recommendation.rejected_count, recommendation.rejected_reasons
    )


# Review summaries (the sonnet call) only cover the top ranked candidates — at
# most this many, so a run never pays for more than a handful of summary calls.
# Candidates with fewer reviews than the floor are skipped (too thin to trust).
_REVIEW_SUMMARY_TOP_N = 3
_SUMMARY_CONCURRENCY = 3  # parallel review-summary LLM calls
_MIN_REVIEWS_FOR_SUMMARY = 5

SummariesById = dict[tuple[str, str], ReviewSummary]


async def _reviews_for(
    scored: ScoredCandidate, active: dict[str, ProductSource], reviews_by_id: ReviewsById
) -> list[RawReview]:
    """Reviews for one candidate: reuse the trust-pass fetch, else its own source.

    The trust pass only fetches Kaspi reviews, so a top-3 candidate from another
    source (e.g. an Ozon listing) has its reviews fetched here — best-effort, a
    failure just yields no reviews (and thus no summary).
    """
    cached = reviews_by_id.get(_candidate_key(scored))
    if cached is not None:
        return cached
    source = active.get(scored.candidate.source)
    if source is None:
        return []
    try:
        return await source.reviews(scored.candidate.source_product_id)
    except Exception as exc:  # noqa: BLE001 - a review fetch must not kill the run
        logger.warning("Review fetch failed for summary: %r", exc)
        return []


def _unverified_features(scored: ScoredCandidate, required_features: Sequence[str]) -> list[str]:
    """Required features the candidate's title/specs do not already show."""
    return [f for f in required_features if not feature_present(f, scored.candidate)]


async def _summarize_top(
    recommendation: Recommendation,
    active: dict[str, ProductSource],
    reviews_by_id: ReviewsById,
    required_features: Sequence[str] = (),
) -> SummariesById:
    """Summarize the top-N ranked candidates' reviews (≥ floor), degrading gracefully."""
    semaphore = asyncio.Semaphore(_SUMMARY_CONCURRENCY)

    async def summarize(scored: ScoredCandidate) -> ReviewSummary | None:
        async with semaphore:
            reviews = await _reviews_for(scored, active, reviews_by_id)
            if len(reviews) < _MIN_REVIEWS_FOR_SUMMARY:
                return None
            features = _unverified_features(scored, required_features)
            try:
                return await summarize_reviews(reviews, features)
            except (LlmError, LlmConfigError) as exc:
                logger.warning("Review summarization failed (%r); skipping candidate", exc)
                return None

    selected = recommendation.ranked[:_REVIEW_SUMMARY_TOP_N]
    results = await asyncio.gather(*(summarize(scored) for scored in selected))
    return {
        _candidate_key(scored): summary
        for scored, summary in zip(selected, results, strict=True)
        if summary is not None
    }


def _apply_summary_penalties(
    recommendation: Recommendation, summaries: SummariesById
) -> Recommendation:
    """Penalize candidates whose summary carries severe signals, then re-assemble.

    Counterfeit suspicion / serious reliability concerns discount the score (and
    confidence) via ``apply_summary_penalty``; re-sorting after may change the
    best pick — that is the whole point. Untouched when no summary bites.
    """
    adjusted: dict[tuple[str, str], ScoredCandidate] = {}
    for scored in recommendation.ranked:
        summary = summaries.get(_candidate_key(scored))
        if summary is None:
            continue
        penalized = apply_summary_penalty(scored, summary)
        if penalized is not scored:
            adjusted[_candidate_key(scored)] = penalized
    if not adjusted:
        return recommendation
    rescored = [adjusted.get(_candidate_key(s), s) for s in recommendation.ranked]
    return assemble_recommendation(
        rescored, recommendation.rejected_count, recommendation.rejected_reasons
    )


def _apply_requirement_checks(
    recommendation: Recommendation, checks_by_id: dict[tuple[str, str], list[RequirementCheck]]
) -> Recommendation:
    """Fold review verdicts on required features into the ranking, then re-assemble."""
    adjusted: dict[tuple[str, str], ScoredCandidate] = {}
    for scored in recommendation.ranked:
        checks = checks_by_id.get(_candidate_key(scored))
        if not checks:
            continue
        updated = apply_requirement_checks(scored, checks)
        if updated is not scored:
            adjusted[_candidate_key(scored)] = updated
    if not adjusted:
        return recommendation
    rescored = [adjusted.get(_candidate_key(s), s) for s in recommendation.ranked]
    return assemble_recommendation(
        rescored, recommendation.rejected_count, recommendation.rejected_reasons
    )


def _naive_checks_by_id(
    recommendation: Recommendation, reviews_by_id: ReviewsById, required_features: Sequence[str]
) -> dict[tuple[str, str], list[RequirementCheck]]:
    """No-LLM stem checks over the fetched reviews of each ranked candidate."""
    checks_by_id: dict[tuple[str, str], list[RequirementCheck]] = {}
    for scored in recommendation.ranked:
        key = _candidate_key(scored)
        reviews = reviews_by_id.get(key)
        features = _unverified_features(scored, required_features)
        if reviews and features:
            checks_by_id[key] = naive_requirement_checks(reviews, features)
    return checks_by_id


async def _review_pass(
    recommendation: Recommendation,
    active: dict[str, ProductSource],
    use_llm: bool,
    with_review_summary: bool,
    required_features: Sequence[str] = (),
) -> tuple[Recommendation, ReviewSummary | None]:
    """Fetch reviews, apply the Kaspi trust re-score, then summarize + penalize.

    Runs while sources are still open. The trust re-score is Kaspi-only (cheap
    endpoint). Summaries cover the top-3 ranked candidates (whatever their
    source); their severity signals penalize scores and may change the best pick.
    The returned summary is the (possibly new) best pick's — that drives the panel.
    """
    reviews_by_id: ReviewsById = {}
    kaspi = active.get(_REVIEW_SOURCE)
    if kaspi is not None:
        reviews_by_id, stats_by_id = await _fetch_top_reviews(kaspi, recommendation)
        recommendation = _apply_review_trust(recommendation, reviews_by_id, stats_by_id)
    review_summary = None
    if with_review_summary and use_llm:
        summaries = await _summarize_top(recommendation, active, reviews_by_id, required_features)
        recommendation = _apply_summary_penalties(recommendation, summaries)
        recommendation = _apply_requirement_checks(
            recommendation, {key: s.requirement_checks for key, s in summaries.items()}
        )
        review_summary = summaries.get(_candidate_key(recommendation.best))
    if not use_llm and required_features:
        checks_by_id = _naive_checks_by_id(recommendation, reviews_by_id, required_features)
        recommendation = _apply_requirement_checks(recommendation, checks_by_id)
    return recommendation, review_summary


# --- Composition-driven categories (docs/PLAN.md) --------------------------


def _sostav_text(candidate: Candidate) -> str:
    """The candidate's composition/состав text for ingredient extraction, if any."""
    return composition_text(candidate.specs)


async def _extract_ingredients_into(
    candidates: list[Candidate], category: str, book: IngredientBook, use_llm: bool
) -> None:
    """Populate ``detected_substances`` + ``concentration`` on each candidate.

    One Jev request over the pool (LLM fallback, see ``llm/ingredients.py``);
    detected names are mapped to the book's canonical substances. Fail-open — a
    failure just leaves candidates with no ingredient data, so the axis scores
    them as "absent" rather than breaking the run.
    """
    if not use_llm or not candidates:
        return
    items = [(c.title, _sostav_text(c)) for c in candidates]
    detections = await extract_ingredients(category, book, items)
    for cand, det in zip(candidates, detections, strict=True):
        canonicals = [book.canonical_for(s) or s for s in det.substances]
        cand.detected_substances = list(dict.fromkeys(c for c in canonicals if c))
        cand.concentration = det.concentration


def _required_substances(book: IngredientBook, constraints: QueryConstraints) -> list[str]:
    """Canonical substances the query targets (from category + required features)."""
    text = " ".join([constraints.category, *constraints.required_features])
    return book.resolve_in_text(text)


_SUBSTANCE_ALTERNATIVES_TOP_N = 3


class NoSubstanceMatch(Exception):
    """No candidate contained the explicitly required active substance.

    A product that lacks the requested active substance must never be
    recommended as the best pick (docs/PLAN.md); when the required-substance
    hard filter empties the pool we refuse to silently fall back and instead
    surface the nearest rejected-by-substance candidates honestly. Carries the
    substances required, how many candidates were checked, and those nearest
    alternatives so the CLI/MCP render the same clear message.
    """

    def __init__(self, substances: list[str], checked: int, alternatives: list[Candidate]) -> None:
        self.substances = substances
        self.checked = checked
        self.alternatives = alternatives
        super().__init__(self.message())

    def message(self) -> str:
        subs = ", ".join(self.substances)
        return (
            f"Не найдено товаров с действующим веществом '{subs}' "
            f"(проверено {self.checked} кандидатов). Ближайшие альтернативы без него:"
        )


def _guard_substance_pool(
    candidates: list[Candidate], constraints: QueryConstraints, required_substances: list[str]
) -> None:
    """Raise ``NoSubstanceMatch`` if the required-substance filter empties the pool.

    Only fires when the pool empties *because of* the substance filter — if it
    empties for another reason (e.g. budget), we stay silent and let ``rank``
    raise its generic "no candidates" error, preserving the existing message.
    """
    passed, rejected = apply_hard_filters(
        candidates,
        constraints,
        required_substances=required_substances,
        substances_verified=True,
    )
    if passed:
        return
    substance_rejects = [
        cand for cand, reason in rejected if reason.startswith(NO_SUBSTANCE_REASON)
    ]
    if not substance_rejects:
        return
    top = sorted(
        substance_rejects,
        key=lambda c: (c.rating or 0.0, c.review_count or 0),
        reverse=True,
    )[:_SUBSTANCE_ALTERNATIVES_TOP_N]
    raise NoSubstanceMatch(required_substances, len(candidates), top)


def _measured(fn):
    """Record a recommend run's duration by outcome, and the ranked pool size when it succeeds."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        outcome = "error"
        try:
            result = await fn(*args, **kwargs)
            outcome = "ok"
            telemetry.OFFERS_RANKED.record(len(result.recommendation.ranked))
            return result
        except NoSubstanceMatch:
            outcome = "no_match"
            raise
        finally:
            telemetry.RECOMMEND_DURATION.record(time.perf_counter() - start, {"outcome": outcome})

    return wrapper


async def _resolve_knowledge(
    category: str, use_llm: bool
) -> tuple[BrandBook | None, ModelBook | None, IngredientBook | None]:
    """Brand/model books plus the ingredient book, only for composition-driven categories."""
    with _stage("knowledge resolve"):
        book, model_book = await resolve_books(category, use_llm=use_llm)
        category_type, ingredient_book = await resolve_composition(category, use_llm=use_llm)
    if book is None and model_book is None:
        logger.info(
            "no brand knowledge for %r — run `buysmarter learn-category %r` to generate it",
            category,
            category,
        )
    return book, model_book, ingredient_book if category_type == COMPOSITION_DRIVEN else None


def _substance_recall(
    ingredient_book: IngredientBook | None, constraints: QueryConstraints
) -> tuple[list[str], list[tuple[str, list[str]]] | None]:
    """Required substances and the extra searches that surface them.

    Composition-driven recall: substance + equivalence-name searches widen the
    Kaspi pool so the actual active-substance products surface (docs/PLAN.md).
    """
    if ingredient_book is None:
        return [], None
    required = _required_substances(ingredient_book, constraints)
    if not required:
        return [], None
    return required, _substance_search_plan(ingredient_book, required)


async def _verify_substances(
    deduped: list[Candidate],
    constraints: QueryConstraints,
    ingredient_book: IngredientBook | None,
    required_substances: list[str],
    use_llm: bool,
) -> bool:
    """Extract состав into the pool; True when the substance filter may bite."""
    if ingredient_book is None:
        return False
    await _extract_ingredients_into(deduped, constraints.category, ingredient_book, use_llm)
    # A required substance is a hard filter only once extraction actually
    # ran (use_llm) — otherwise absence is unknowable, not proven.
    verified = use_llm and bool(required_substances)
    if verified:
        _guard_substance_pool(deduped, constraints, required_substances)
    return verified


@_measured
async def recommend(
    query: str,
    *,
    city: str | None = None,
    budget: int | None = None,
    use_llm: bool = True,
    trusted_only: bool = False,
    source: ProductSource | None = None,
    sources: list[str] | None = None,
    ozon_source: ProductSource | None = None,
    olx_source: ProductSource | None = None,
    persist: bool = True,
    parsed: QueryConstraints | None = None,
    with_review_summary: bool = True,
) -> RecommendResult:
    """Run the full multi-source recommend pipeline.

    ``sources`` selects the marketplaces to query (defaults to Kaspi only for
    single-source callers; the CLI passes all three). ``source`` / ``ozon_source``
    / ``olx_source`` inject adapters (used in tests). Kaspi + Ozon feed the
    ranking pool; OLX contributes the advisory used-market section only.
    ``parsed`` lets a caller reuse a constraints parse it already did (the CLI
    parses once to decide whether to prompt for a budget, then passes the
    result back in here instead of parsing the query a second time).
    """
    total_start = time.perf_counter()
    with _stage("query parse"):
        base_constraints = parsed if parsed is not None else await _parse(query, use_llm)
    resolved_city, city_defaulted = await _resolve_city(base_constraints.city, city)
    constraints = _apply_overrides(base_constraints, resolved_city, budget, trusted_only)
    active = _resolve_sources(sources or ["kaspi"], source, ozon_source, olx_source)
    book, model_book, ingredient_book = await _resolve_knowledge(constraints.category, use_llm)
    composition = ingredient_book is not None
    city_id = _city_id(constraints.city)
    required_substances, substance_plan = _substance_recall(ingredient_book, constraints)

    review_summary: ReviewSummary | None = None
    try:
        with _stage("collect"):
            collected = await _gather(
                active, constraints.category, city_id, book, constraints.budget_max, substance_plan
            )
        new_candidates, persist_rows = _merge_new_goods(collected)
        # Attach 30-day price history (from prior runs' snapshots) before ranking
        # so the price axis can reward a genuine dip; best-effort, no-op keyless.
        await _attach_price_history(new_candidates)
        protected_ids = (
            _substance_confirmed_ids(new_candidates, ingredient_book, required_substances)
            if composition and required_substances
            else set()
        )
        with _stage("relevance filter"):
            new_candidates = await _filter_relevant_candidates(
                new_candidates, constraints.category, use_llm, protected_ids
            )
        used_candidates = cast(list[Candidate], collected.get("olx") or [])
        deduped, notes = dedupe_cross_source(new_candidates, model_book)
        if is_smartphone_category(constraints.category):
            with _stage("kimovil"):
                await enrich_with_kimovil(deduped)
        substances_verified = await _verify_substances(
            deduped, constraints, ingredient_book, required_substances, use_llm
        )
        with _stage("ranking"):
            recommendation = rank(
                deduped,
                constraints,
                composition=composition,
                required_substances=required_substances,
                substances_verified=substances_verified,
                query=query,
            )
        # Second pass (still inside the try so sources stay open): fetch reviews
        # for the top Kaspi picks, discount fake-looking review axes, re-sort,
        # then summarize the (possibly changed) best pick from its own source.
        with _stage("review summaries"):
            recommendation, review_summary = await _review_pass(
                recommendation,
                active,
                use_llm,
                with_review_summary,
                constraints.required_features,
            )
    finally:
        for src in active.values():
            await _aclose(src)

    if persist and persist_rows:
        await _persist(persist_rows, constraints.category)

    apply_cross_notes(recommendation, notes)

    used_listings = await used_market(
        recommendation, used_candidates, constraints, book, model_book, use_llm
    )
    logger.info("stage %s took %.2fs", "total", time.perf_counter() - total_start)
    return RecommendResult(
        recommendation=recommendation,
        constraints=constraints,
        used_listings=used_listings,
        city_defaulted=city_defaulted,
        review_summary=review_summary,
    )


def _apply_overrides(
    constraints: QueryConstraints, city: str | None, budget: int | None, trusted_only: bool
) -> QueryConstraints:
    updates: dict = {}
    if city:
        updates["city"] = city
    if budget is not None:
        updates["budget_max"] = budget
    if trusted_only:
        updates["trusted_brands_only"] = True
    constraints = constraints.model_copy(update=updates) if updates else constraints
    return _drop_category_implied_features(constraints)


# Item condition is not a spec: "б/у" as a hard feature rejects every new Kaspi
# product, and the used market is OLX's job, which always runs.
_CONDITION_WORDS = frozenset({"б/у", "бу", "б.у.", "used", "новый", "новое", "new"})


def _word_tokens(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"\w+", text)]


def _drop_category_implied_features(constraints: QueryConstraints) -> QueryConstraints:
    """Drop required features already covered by the category, and condition words.

    The LLM parser turns "TWS наушники" into category="TWS наушники" +
    required_features=["TWS"] — but a category-defining jargon token is not a
    spec to string-match: Kaspi titles the same product "Наушники Huawei
    Freebuds SE 4" with no "TWS" anywhere, so the hard filter would reject
    exactly the on-category branded candidates recall just found (live-run
    regression). Category membership is the relevance filter's job; only
    features that add information beyond the category (IPX7, ANC...) remain
    hard requirements. A multi-word feature counts as covered when all its words
    are in the category ("iPhone 13" in "смартфон iPhone 13").
    """
    category_tokens = set(_word_tokens(constraints.category))
    kept = [
        f
        for f in constraints.required_features
        if f.strip().lower() not in _CONDITION_WORDS and not set(_word_tokens(f)) <= category_tokens
    ]
    if len(kept) == len(constraints.required_features):
        return constraints
    return constraints.model_copy(update={"required_features": kept})


# Category-relevance filter (drop non-products before ranking). Marketplace
# search is loose — accessories/spare parts pollute rankings (a Dyson battery
# ranked #1 for "робот пылесос"). Both passes fail open; never drop below this.
_MIN_RELEVANT_CANDIDATES = 3
_LLM_DROP_REASON = "llm-irrelevant"

Drop = tuple[Candidate, str]


def _accessory_drops(
    candidates: list[Candidate], category: str
) -> tuple[list[Candidate], list[Drop]]:
    """Free heuristic pre-pass: split candidates into kept + (dropped, reason)."""
    kept: list[Candidate] = []
    dropped: list[Drop] = []
    for cand in candidates:
        reason = accessory_reason(category, cand.title)
        if reason is None:
            kept.append(cand)
        else:
            dropped.append((cand, reason))
            logger.info("relevance: drop %r (accessory marker %r)", cand.title, reason)
    return kept, dropped


async def _llm_drops(
    candidates: list[Candidate], category: str
) -> tuple[list[Candidate], list[Drop]]:
    """LLM pass: classify surviving candidates, split into kept + dropped."""
    flags = await filter_relevant(category, [c.title for c in candidates])
    kept = [c for c, keep in zip(candidates, flags, strict=True) if keep]
    dropped = [(c, _LLM_DROP_REASON) for c, keep in zip(candidates, flags, strict=True) if not keep]
    for cand, _ in dropped:
        logger.info("relevance: drop %r (%s)", cand.title, _LLM_DROP_REASON)
    return kept, dropped


def _restore_min(
    kept: list[Candidate], dropped: list[Drop]
) -> tuple[list[Candidate], list[Candidate]]:
    """Fail-open guard: never let the pool fall below the minimum.

    If filtering left fewer than the floor, the highest-rated dropped candidates
    are added back until the floor is met. Returns ``(pool, restored)``.
    """
    if len(kept) >= _MIN_RELEVANT_CANDIDATES or not dropped:
        return kept, []
    need = _MIN_RELEVANT_CANDIDATES - len(kept)
    best_first = sorted(
        (c for c, _ in dropped),
        key=lambda c: (c.rating or 0.0, c.review_count or 0),
        reverse=True,
    )
    restored = best_first[:need]
    return kept + restored, restored


def _log_drop_summary(dropped: list[Drop], restored: list[Candidate], kept_count: int) -> None:
    """Log how many candidates were dropped and why (count per reason)."""
    if not dropped:
        return
    counts = Counter(reason for _, reason in dropped)
    breakdown = ", ".join(f"{reason}={n}" for reason, n in counts.most_common())
    logger.info(
        "relevance filter: %d dropped (%s), %d restored to meet floor, %d kept",
        len(dropped),
        breakdown,
        len(restored),
        kept_count,
    )


async def _filter_relevant_candidates(
    candidates: list[Candidate],
    category: str,
    use_llm: bool,
    protected_ids: set[str] | None = None,
) -> list[Candidate]:
    """Drop accessories/non-products before ranking; never below the floor.

    Heuristic always runs; the LLM pass runs only with ``use_llm`` and a pool
    still larger than the floor (nothing to gain otherwise). Fully fail-open.

    ``protected_ids`` (source_product_ids of substance-confirmed candidates in
    composition-driven categories) bypass both passes: an adapalene gel proven
    to carry the requested substance must not be evicted by a "крем vs гель"
    form-factor relevance drop — that is exactly what defeated recall before.
    """
    protected_ids = protected_ids or set()
    protected = [c for c in candidates if c.source_product_id in protected_ids]
    rest = [c for c in candidates if c.source_product_id not in protected_ids]
    kept, dropped = _accessory_drops(rest, category)
    if use_llm and len(kept) > _MIN_RELEVANT_CANDIDATES:
        kept, llm_dropped = await _llm_drops(kept, category)
        dropped.extend(llm_dropped)
    pool, restored = _restore_min(kept, dropped)
    _log_drop_summary(dropped, restored, len(pool))
    return protected + pool


def _merge_new_goods(collected: dict[str, object]) -> tuple[list[Candidate], list[tuple]]:
    """Combine Kaspi + Ozon collector output into one ranking pool + persist rows."""
    candidates: list[Candidate] = []
    rows: list[tuple] = []
    for name in ("kaspi", "ozon"):
        result = collected.get(name)
        if not result:
            continue
        source_candidates, source_rows = cast(tuple[list[Candidate], list[tuple]], result)
        candidates.extend(source_candidates)
        rows.extend(source_rows)
    return candidates, rows


# --- Price-tracking refresh (docs/PLAN.md фаза 4 "history цен") -------------
# `refresh` re-runs the search-only slice for every tracked category and records
# a fresh snapshot per product — no ranking, no LLM, sources scanned gently.

_REFRESH_TOP_DROPS = 5  # biggest drops surfaced per category summary


class PriceChange(BaseModel):
    """A product whose min price moved between the previous and new snapshot."""

    title: str
    old_price: int
    new_price: int
    delta: int  # new - old (negative == a price drop)


class CategoryRefresh(BaseModel):
    """Compact per-category outcome of a ``refresh`` run."""

    category: str
    product_count: int
    changed: int
    drops: list[PriceChange] = Field(default_factory=list)


async def _persist_refresh(rows: list[tuple], category: str) -> list[PriceChange]:
    """Persist rows + snapshots, returning the price changes vs the prior snapshot."""
    changes: list[PriceChange] = []
    async with async_session() as session:
        for product, specs, offers in rows:
            db_product = await upsert_product(session, product)
            if specs:
                db_product.specs = specs
                await session.commit()
            if offers:
                await add_offers(session, db_product.id, offers)
            snapshot = _snapshot_values(product, offers)
            if snapshot is None:
                continue
            previous = await latest_snapshot(session, db_product.id)
            new_min, offer_count = snapshot
            await add_price_snapshot(
                session,
                db_product.id,
                new_min,
                offer_count,
                review_count=product.review_count,
                rating=product.rating,
            )
            if previous is not None and previous.min_price != new_min:
                changes.append(
                    PriceChange(
                        title=product.title,
                        old_price=previous.min_price,
                        new_price=new_min,
                        delta=new_min - previous.min_price,
                    )
                )
        await track_category(session, category)
    return changes


async def refresh_category(category: str) -> CategoryRefresh:
    """Search-only re-scan of one category: persist snapshots, report changes.

    All sources, naive (no brand-targeted searches, no ranking, no LLM). The
    persistence records a new snapshot per product and compares it to the prior
    one to surface price movements.
    """
    active = _resolve_sources(list(ALL_SOURCES), None, None, None)
    city, _ = await _resolve_city(None, None)
    city_id = _city_id(city)
    try:
        collected = await _gather(active, category, city_id, None, None)
        _, persist_rows = _merge_new_goods(collected)
    finally:
        for src in active.values():
            await _aclose(src)
    changes = await _persist_refresh(persist_rows, category) if persist_rows else []
    drops = sorted((c for c in changes if c.delta < 0), key=lambda c: c.delta)
    return CategoryRefresh(
        category=category,
        product_count=len(persist_rows),
        changed=len(changes),
        drops=drops[:_REFRESH_TOP_DROPS],
    )


async def refresh_tracked() -> list[CategoryRefresh]:
    """Refresh every tracked category, sequentially (gentle on the sources)."""
    async with async_session() as session:
        categories = await get_tracked_categories(session)
    results: list[CategoryRefresh] = []
    for category in categories:
        try:
            results.append(await refresh_category(category))
        except Exception as exc:  # noqa: BLE001 - one bad category must not abort refresh
            logger.warning("Refresh failed for %r: %r", category, exc, exc_info=True)
    return results


# --- Product price history (the `history` command) -------------------------


class SnapshotRow(BaseModel):
    """One captured price point for a product (rendered by ``history``)."""

    captured_at: str
    min_price: int
    offer_count: int


class ProductHistory(BaseModel):
    """A product and its snapshot timeline, newest first."""

    title: str
    source: str
    url: str | None = None
    snapshots: list[SnapshotRow] = Field(default_factory=list)


async def product_history(term: str) -> list[ProductHistory]:
    """Look up products by title/url substring and return their snapshot history.

    Best-effort — returns ``[]`` when Postgres is unreachable.
    """
    try:
        async with async_session() as session:
            products = await find_products(session, term)
            histories: list[ProductHistory] = []
            for product in products:
                snapshots = await list_snapshots(session, product.id)
                histories.append(
                    ProductHistory(
                        title=product.title,
                        source=product.source,
                        url=product.url,
                        snapshots=[
                            SnapshotRow(
                                captured_at=s.captured_at.isoformat(timespec="minutes"),
                                min_price=s.min_price,
                                offer_count=s.offer_count,
                            )
                            for s in snapshots
                        ],
                    )
                )
            return histories
    except Exception as exc:  # noqa: BLE001 - history is a read-only nicety
        logger.warning("Product-history lookup failed (DB unavailable?): %r", exc)
        return []
