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
import logging
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any, cast

from market_sources.base import ProductSource
from pydantic import BaseModel, Field

from buysmarter import telemetry
from buysmarter.config import get_settings
from buysmarter.db.base import async_session
from buysmarter.db.repo import (
    get_preference,
)
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.ingredients import IngredientBook, composition_text
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.knowledge.store import (
    COMPOSITION_DRIVEN,
    resolve_books,
    resolve_composition,
)
from buysmarter.llm.adapter import LlmConfigError, LlmError, current_route
from buysmarter.llm.ingredients import extract_ingredients
from buysmarter.llm.prompts import parse_query, parse_query_naive
from buysmarter.llm.relevance import accessory_reason, filter_relevant
from buysmarter.llm.review_summary import ReviewSummary
from buysmarter.ranking.engine import rank
from buysmarter.ranking.filters import NO_SUBSTANCE_REASON, apply_hard_filters
from buysmarter.ranking.models import (
    Candidate,
    QueryConstraints,
    Recommendation,
)
from buysmarter.services.collect import (
    aclose,
    attach_price_history,
    gather_sources,
    kaspi_city_id,
    merge_new_goods,
    resolve_sources,
    save_snapshots,
    stage,
    substance_search_plan,
)
from buysmarter.services.matching import (
    UsedListing,
    apply_cross_notes,
    dedupe_cross_source,
    used_market,
)
from buysmarter.services.phone_specs import enrich_with_kimovil, is_smartphone_category
from buysmarter.services.review_pass import review_pass

logger = logging.getLogger(__name__)

# Last-resort city when neither the query, --city, nor a stored preference
# yields one (docs/INIT_DATA.md, most users are in Almaty).
_DEFAULT_CITY = "Алматы"
_CITY_PREFERENCE_KEY = "city"
# What a run with no LLM route (no key, no claude CLI) does without the LLM.
NO_LLM_STEPS = ("query parsing", "review summaries", "ingredient extraction")


class RecommendResult(BaseModel):
    """Pipeline output: the ranking, the constraints used, and used listings."""

    recommendation: Recommendation
    constraints: QueryConstraints
    used_listings: list[UsedListing] = Field(default_factory=list)
    city_defaulted: bool = False
    review_summary: ReviewSummary | None = None
    # Why ``review_summary`` is None although summaries ran (few review texts, an LLM error).
    review_summary_note: str | None = None
    # No brand/model knowledge for the category: every brand scores as unknown.
    knowledge_missing: bool = False
    # Set when the run went no-LLM because nothing is configured (not on an explicit opt-out).
    skipped_llm_steps: list[str] = Field(default_factory=list)
    # Selected marketplaces left out for lack of a browser (only Ozon needs one).
    unavailable_sources: list[str] = Field(default_factory=list)


def _no_llm_route() -> bool:
    return current_route(get_settings()) is None


async def _parse(query: str, use_llm: bool) -> QueryConstraints:
    if not use_llm or _no_llm_route():
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


async def resolve_city(parsed_city: str | None, override: str | None) -> tuple[str, bool]:
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


def _measured[**P](
    fn: Callable[P, Awaitable[RecommendResult]],
) -> Callable[P, Awaitable[RecommendResult]]:
    """Record a recommend run's duration by outcome, and the ranked pool size when it succeeds."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> RecommendResult:
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
    with stage("knowledge resolve"):
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
    return required, substance_search_plan(ingredient_book, required)


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
    skipped_llm_steps = list(NO_LLM_STEPS) if use_llm and _no_llm_route() else []
    use_llm = use_llm and not skipped_llm_steps
    with stage("query parse"):
        base_constraints = parsed if parsed is not None else await _parse(query, use_llm)
    resolved_city, city_defaulted = await resolve_city(base_constraints.city, city)
    constraints = _apply_overrides(base_constraints, resolved_city, budget, trusted_only)
    requested = sources or ["kaspi"]
    # off the loop: picking Ozon's browser may launch one to probe it
    active = await asyncio.to_thread(resolve_sources, requested, source, ozon_source, olx_source)
    book, model_book, ingredient_book = await _resolve_knowledge(constraints.category, use_llm)
    composition = ingredient_book is not None
    city_id = kaspi_city_id(constraints.city)
    required_substances, substance_plan = _substance_recall(ingredient_book, constraints)

    review_summary: ReviewSummary | None = None
    review_summary_note: str | None = None
    try:
        with stage("collect"):
            collected = await gather_sources(
                active, constraints.category, city_id, book, constraints.budget_max, substance_plan
            )
        new_candidates, persist_rows = merge_new_goods(collected)
        # Attach 30-day price history (from prior runs' snapshots) before ranking
        # so the price axis can reward a genuine dip; best-effort, no-op keyless.
        await attach_price_history(new_candidates)
        protected_ids = (
            _substance_confirmed_ids(new_candidates, ingredient_book, required_substances)
            if composition and required_substances
            else set()
        )
        with stage("relevance filter"):
            new_candidates = await _filter_relevant_candidates(
                new_candidates, constraints.category, use_llm, protected_ids
            )
        used_candidates = cast(list[Candidate], collected.get("olx") or [])
        deduped, notes = dedupe_cross_source(new_candidates, model_book)
        if is_smartphone_category(constraints.category):
            with stage("kimovil"):
                await enrich_with_kimovil(deduped)
        substances_verified = await _verify_substances(
            deduped, constraints, ingredient_book, required_substances, use_llm
        )
        with stage("ranking"):
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
        with stage("review summaries"):
            recommendation, review_summary, review_summary_note = await review_pass(
                recommendation,
                active,
                use_llm,
                with_review_summary,
                constraints.required_features,
            )
    finally:
        for src in active.values():
            await aclose(src)

    if persist and persist_rows:
        await save_snapshots(persist_rows, constraints.category)

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
        review_summary_note=review_summary_note,
        knowledge_missing=book is None and model_book is None,
        skipped_llm_steps=skipped_llm_steps,
        unavailable_sources=[name for name in requested if name not in active],
    )


def _apply_overrides(
    constraints: QueryConstraints, city: str | None, budget: int | None, trusted_only: bool
) -> QueryConstraints:
    updates: dict[str, Any] = {}
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
