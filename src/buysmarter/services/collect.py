"""Collect raw offers from the marketplaces: Kaspi, Ozon and OLX adapters, the
brand- and substance-targeted extra searches, snapshot persistence and price history.
"""

import asyncio
import inspect
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast

import httpx
from market_sources.base import ProductSource, RawOffer, RawProduct
from market_sources.cities import kaspi_city_id, olx_city_id
from market_sources.kaspi.client import KaspiClient
from market_sources.kaspi.source import KaspiSource
from market_sources.olx.source import OlxSource
from market_sources.ozon.browser import OzonBrowser
from market_sources.ozon.source import OzonSource

from buysmarter import telemetry
from buysmarter.config import get_settings
from buysmarter.db.base import async_session
from buysmarter.db.repo import (
    add_offers,
    add_price_snapshot,
    get_product,
    price_history,
    review_count_history,
    track_category,
    upsert_product,
)
from buysmarter.knowledge.brands import BrandBook
from buysmarter.normalize.candidate import to_candidate
from buysmarter.ranking.models import (
    Candidate,
    PriceHistory,
)
from buysmarter.ranking.used_defects import is_for_parts
from buysmarter.ranking.velocity import review_velocity
from buysmarter.services.matching import (
    ad_text,
    is_jargon_only,
    matches_category_or_stripped,
    strip_latin_tokens,
    title_matches_category,
)
from buysmarter.services.ozon_browser import channel, search_browser

logger = logging.getLogger(__name__)

# A product to persist: the raw listing, its specs and its seller offers.
ProductRow = tuple[RawProduct, dict[str, str], list[RawOffer]]


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


@contextmanager
def stage(name: str, source: str | None = None) -> Iterator[None]:
    """Log a pipeline stage's start and its elapsed wall time (INFO).

    The ``progress_*`` extra fields drive the desktop app's progress display, so it never parses
    the message text: ``progress_state`` goes running, then done, failed or cancelled.
    """
    progress = {"progress_stage": name, "progress_source": source}
    logger.info("stage %s started", name, extra={**progress, "progress_state": "running"})
    start = time.perf_counter()
    state = "failed"
    try:
        yield
        state = "done"
    except asyncio.CancelledError:
        state = "cancelled"
        raise
    finally:
        took = time.perf_counter() - start
        logger.info("stage %s took %.2fs", name, took, extra={**progress, "progress_state": state})


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
            and _card_brand_agrees(card, book, canonical)
            and (jargon_only or title_matches_category(card.title, category))
        ]
        found.extend(matched[:_BRAND_SEARCH_TOP_N])
    return found


def _card_brand_agrees(card: RawProduct, book: BrandBook, canonical: str) -> bool:
    """False when the card's own brand field names someone else ("FASHION" selling
    "Yeezy"): a brand search must not recruit look-alikes as that brand's recall."""
    card_brand = getattr(card, "brand", None)
    return not card_brand or book.resolve(card_brand)[0] == canonical


# Extra searches a capability asks for (composition: each substance and its
# equivalent products, which hide behind their brand names): mirror the
# brand-targeted recall and keep only cards whose title names a needle.
_EXTRA_SEARCH_TOP_N = 3  # top cards per extra search kept


async def _extra_search_products(
    source: ProductSource, plan: list[tuple[str, list[str]]], city_id: str | None
) -> list[RawProduct]:
    """Run each extra search; keep cards whose title names one of its needles.

    Title confirmation is recall-stage relevance only; the capability's own checks
    downstream (composition: состав extraction + hard filter) decide.
    """
    found: list[RawProduct] = []
    for query, needles in plan:
        try:
            cards = await source.search(query, city=city_id)
        except Exception as exc:  # noqa: BLE001 - one bad extra search shouldn't kill recall
            logger.warning("Extra search failed for %r: %r", query, exc)
            continue
        lowered_needles = [n.lower() for n in needles]
        matched = [card for card in cards if any(n in card.title.lower() for n in lowered_needles)]
        found.extend(matched[:_EXTRA_SEARCH_TOP_N])
    return found


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


async def _safe_specs(source: ProductSource, product_id: str) -> tuple[dict[str, str], bool]:
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


async def _safe_offers(source: ProductSource, product_id: str) -> list[RawOffer]:
    """Best-effort Kaspi offers fetch — one bad product id must not skip the whole source.

    Logged at INFO, same reasoning as ``_safe_specs`` — a per-item failure is
    expected noise, not console-worthy on its own.
    """
    try:
        return await source.offers(product_id)
    except Exception as exc:  # noqa: BLE001 - one bad product must not kill the source
        logger.info("Kaspi offers fetch failed for product %s: %r", product_id, exc)
        return []


async def _kaspi_search_pool(
    source: ProductSource,
    category: str,
    city_id: str | None,
    book: BrandBook | None,
    extra_searches: list[tuple[str, list[str]]] | None,
) -> tuple[list[RawProduct], list[RawProduct]]:
    """Targeted (substance, then brand) hits and the rest of the main search, deduped."""
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
    if extra_searches:
        targeted += await _extra_search_products(source, extra_searches, city_id)
        search_count += len(extra_searches)
    if book is not None:
        targeted += await _brand_targeted_products(source, category, city_id, book)
        search_count += len(_brand_search_plan(book))
    targeted = _dedupe_products(targeted)[:_MAX_TARGETED_CARDS]
    targeted_ids = {p.product_id for p in targeted}
    rest = [p for p in products if p.product_id not in targeted_ids]
    logger.info(
        "kaspi: %d searches issued (main+stripped+substance+brand), %d candidates gathered",
        search_count,
        len(targeted) + len(rest[:_MAX_CARDS]),
    )
    return targeted, rest


async def _collect_kaspi(
    source: ProductSource,
    category: str,
    city_id: str | None,
    book: BrandBook | None,
    extra_searches: list[tuple[str, list[str]]] | None = None,
) -> tuple[list[Candidate], list[ProductRow]]:
    """Kaspi: brand/substance-targeted + main search, specs for all, offers for top N."""
    targeted, rest = await _kaspi_search_pool(source, category, city_id, book, extra_searches)
    main = rest[:_MAX_CARDS]
    top = targeted + main
    # Offer fetch (a real HTTP call each) is split evenly across the two
    # sub-pools instead of "first _OFFERS_FOR of the combined list": targeted
    # hits are always listed first, so an index-only cutoff always gave
    # targeted candidates their true cheapest-offer price while every
    # main-pool card kept the (usually higher) search-card price — a
    # systematic bias in the pool-relative price axis (docs/PLAN.md
    # "Ranking-фиксы"). A pool too small for its half leaves the rest to the other.
    targeted_n = min(len(targeted), max(_OFFERS_FOR // 2, _OFFERS_FOR - len(main)))
    offer_eligible = targeted[:targeted_n] + main[: _OFFERS_FOR - targeted_n]
    offer_eligible_ids = {p.product_id for p in offer_eligible}
    semaphore = asyncio.Semaphore(_KASPI_DETAIL_CONCURRENCY)

    async def fetch_details(product: RawProduct) -> tuple[dict[str, str], bool, list[RawOffer]]:
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
    rows: list[ProductRow] = []
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
    source: ProductSource, category: str, book: BrandBook | None, budget: int | None
) -> tuple[list[Candidate], list[ProductRow]]:
    """Ozon: main search only, price-capped at ``budget`` (an uncapped
    relevance-ranked first page can sit entirely above it); specs (a browser
    nav each) for the very top few. Off-category cards are dropped: a cheap
    price cap fills the page with look-alikes (a ukulele for "гитара")."""
    products = await _budget_search(source, category, budget)
    on_category = [p for p in products if matches_category_or_stripped(p.title, category, book)]
    top = on_category[:_OZON_MAX_CARDS]
    candidates: list[Candidate] = []
    rows: list[ProductRow] = []
    for index, product in enumerate(top):
        # Per-item guard: one browser hiccup must not discard the whole Ozon pool.
        specs: dict[str, str] = {}
        if index < _OZON_SPECS_FOR:
            specs, _ok = await _safe_specs(source, product.product_id)
        candidates.append(to_candidate(product, specs, [], book))
        rows.append((product, specs, []))
    return candidates, rows


async def _budget_search(
    source: ProductSource, query: str, budget: int | None, city: str | None = None
) -> list[RawProduct]:
    """One search, passing ``budget`` and ``city`` only to adapters whose search accepts them.

    The real ``OlxSource``/``OzonSource.search`` take a ``budget`` (a server-side price cap);
    injected test doubles may not, so we pass it only when the signature has it.
    """
    params = inspect.signature(source.search).parameters
    given = {"budget": budget, "city": city}
    kwargs = {k: v for k, v in given.items() if v is not None and k in params}
    return await source.search(query, **kwargs)  # type: ignore[arg-type]


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
    tokens: list[str] = [
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
    source: ProductSource,
    category: str,
    budget: int | None,
    book: BrandBook | None = None,
    city: str | None = None,
) -> list[RawProduct]:
    """OLX search broadened by the latin-stripped variant + head noun, deduped.

    The full-category hits come first (most relevant); the latin-jargon-stripped
    variant ("TWS наушники" -> "наушники") and head-noun hits widen the pool so
    branded used deals hidden behind a narrow/jargon-heavy query surface.
    """
    products = await _budget_search(source, category, budget, city)
    stripped = strip_latin_tokens(category, book)
    if stripped is not None:
        more = await _budget_search(source, stripped, budget, city)
        products = _dedupe_products(products + more)
    noun = _head_noun(category, book)
    if noun is not None:
        products = _dedupe_products(products + await _budget_search(source, noun, budget, city))
    return products


def _is_used(condition: str | None) -> bool:
    """True when an OLX listing's condition reads as used (б/у), not new."""
    if not condition:
        return False
    low = condition.lower()
    return any(kw in low for kw in ("б/у", "б.у", "бу", "used", "уценк"))


async def _collect_olx(
    source: ProductSource,
    category: str,
    book: BrandBook | None,
    budget: int | None,
    city_id: str | None = None,
) -> list[Candidate]:
    """OLX: scan the top listings, keep only used, on-category ones as match candidates."""
    products = await _olx_search(source, category, budget, book, city_id)
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


def resolve_sources(
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
    factories: dict[str, Callable[[], ProductSource | None]] = {
        "kaspi": lambda: kaspi or KaspiSource(KaspiClient(proxy=settings.kaspi_proxy)),
        "ozon": lambda: ozon or _ozon_source(settings.ozon_base_url),
        "olx": lambda: olx or OlxSource(),
    }
    built = {name: factories[name]() for name in names if name in factories}
    return {name: source for name, source in built.items() if source is not None}


def _ozon_source(base_url: str) -> ProductSource | None:
    """Ozon on the selected browser; None (Ozon left out) when there is none to use."""
    # ponytail: the first call probes browsers synchronously (~1 s per launch, once per process)
    # on the event loop; make the probe async if that pause ever shows.
    browser = search_browser()
    if browser is None:
        return None
    return OzonSource(OzonBrowser(base_url=base_url, channel=channel(browser)))


async def _safe[T](name: str, coro: Awaitable[T]) -> T | None:
    """Await a source coroutine, swallowing failures so one source can't kill the run."""
    try:
        with stage(f"collect[{name}]", source=name):
            result = await coro
    except Exception as exc:  # noqa: BLE001 - one dead source must not abort the pipeline
        # A server answer (Kaspi's 503 maintenance page) is an outage, not a bug: no stack.
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            logger.warning("Source %r failed, skipping it: site answered HTTP %d", name, status)
        else:
            logger.warning("Source %r failed, skipping it: %r", name, exc, exc_info=True)
        telemetry.SOURCE_REQUESTS.add(1, {"source": name, "outcome": "error"})
        return None
    telemetry.SOURCE_REQUESTS.add(1, {"source": name, "outcome": "ok"})
    return result


def snapshot_values(product: RawProduct, offers: list[RawOffer]) -> tuple[int, int] | None:
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


async def save_snapshots(rows: list[ProductRow], category: str | None = None) -> None:
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
                snapshot = snapshot_values(product, offers)
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


async def attach_price_history(candidates: list[Candidate]) -> None:
    """Best-effort: attach each candidate's 30-day price history from the DB.

    One short-lived session, sequential lookups (gentle). Without a local store the
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


async def aclose(source: ProductSource) -> None:
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


async def gather_sources(
    active: dict[str, ProductSource],
    category: str,
    city: str | None,
    book: BrandBook | None,
    budget: int | None,
    extra_searches: list[tuple[str, list[str]]] | None = None,
) -> dict[str, object]:
    """Run every active source's collector concurrently, isolating failures.

    ``city`` is a name; an unknown one searches the site's default (Almaty). Ozon gets no city.
    """
    coros: dict[str, Awaitable[object]] = {}
    if "kaspi" in active:
        kaspi_city = kaspi_city_id(city)
        coros["kaspi"] = _collect_kaspi(active["kaspi"], category, kaspi_city, book, extra_searches)
    if "ozon" in active:
        coros["ozon"] = _collect_ozon(active["ozon"], category, book, budget)
    if "olx" in active:
        coros["olx"] = _collect_olx(active["olx"], category, book, budget, olx_city_id(city))
    names = list(coros)
    results = await asyncio.gather(*(_safe(n, coros[n]) for n in names))
    return dict(zip(names, results, strict=True))


def merge_new_goods(collected: dict[str, object]) -> tuple[list[Candidate], list[ProductRow]]:
    """Combine Kaspi + Ozon collector output into one ranking pool + persist rows."""
    candidates: list[Candidate] = []
    rows: list[ProductRow] = []
    for name in ("kaspi", "ozon"):
        result = collected.get(name)
        if not result:
            continue
        source_candidates, source_rows = cast(tuple[list[Candidate], list[ProductRow]], result)
        candidates.extend(source_candidates)
        rows.extend(source_rows)
    return candidates, rows
