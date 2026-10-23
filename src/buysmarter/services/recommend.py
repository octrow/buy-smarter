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
import statistics
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from market_sources.base import ProductSource
from pydantic import BaseModel, Field

from buysmarter import telemetry
from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.classify import ensure_profile
from buysmarter.capabilities.composition import INGREDIENTS_UNCHECKED
from buysmarter.capabilities.composition import NoSubstanceMatch as NoSubstanceMatch  # re-export
from buysmarter.capabilities.flow import ActiveCapabilities, activate, read_query
from buysmarter.capabilities.provenance import CapabilityState, capability_states
from buysmarter.config import get_settings
from buysmarter.db.base import async_session
from buysmarter.db.repo import (
    get_preference,
)
from buysmarter.knowledge.profile import ResolvedProfile
from buysmarter.llm.adapter import LlmConfigError, LlmError, current_route
from buysmarter.llm.prompts import parse_query, parse_query_naive
from buysmarter.llm.relevance import (
    KEEP_THRESHOLD,
    USED_KEEP_THRESHOLD,
    accessory_reason,
    filter_relevant,
)
from buysmarter.llm.review_summary import ReviewSummary
from buysmarter.normalize.candidate import attach_reference_prices
from buysmarter.ranking.engine import NoCandidatesPassed, rank
from buysmarter.ranking.models import (
    Candidate,
    QueryConstraints,
    Recommendation,
)
from buysmarter.services.collect import (
    ProductRow,
    aclose,
    attach_price_history,
    gather_sources,
    merge_new_goods,
    resolve_sources,
    save_snapshots,
    stage,
)
from buysmarter.services.matching import (
    CrossSourceNote,
    UsedListing,
    apply_cross_notes,
    dedupe_cross_source,
    names_query_model,
    used_market,
    used_market_without_pick,
)
from buysmarter.services.official_stores import official_candidates
from buysmarter.services.review_pass import review_pass
from buysmarter.services.understanding import (
    NeedsClarification,
    NothingToSearch,
    apply_answer,
    ask_capability,
    restricted_kind,
    restricted_note,
    settle_request,
)

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
    # Composition category with a ranked candidate whose состав was not checked.
    ingredients_unchecked: bool = False
    # Restricted goods (alcohol, vapes...): the warning shown before the results.
    restricted_note: str | None = None
    # The option searched for the user on the last clarification round ("крем для рук").
    assumed: str | None = None
    # Each registry capability's state for this query and who decided it (phase 3, decision 13).
    capabilities: list[CapabilityState] = Field(default_factory=list)
    # The stored record the profile lookup settled on; the switches' parent when it is not the
    # request's own.
    profile_found: str | None = None


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


class NoNewPick(ValueError):
    """No new product passed the hard filters; OLX may still have ads within the budget.

    Carries why the new products were rejected and those ads, so the CLI, the app, the API jobs
    and MCP show them instead of an empty error (``cli.report.no_new_pick_text``). A
    ``ValueError``, so a caller that does not know it still reports "nothing to recommend".
    """

    def __init__(self, rejected_reasons: dict[str, int], used_listings: list[UsedListing]) -> None:
        self.rejected_reasons = rejected_reasons
        self.used_listings = used_listings
        super().__init__("no candidates passed hard filters")


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
        except NeedsClarification:
            outcome = "clarify"
            raise
        except NothingToSearch:
            outcome = "nothing_to_search"
            raise
        except (NoSubstanceMatch, NoNewPick):
            outcome = "no_match"
            raise
        finally:
            telemetry.RECOMMEND_DURATION.record(time.perf_counter() - start, {"outcome": outcome})

    return wrapper


async def _resolve_knowledge(constraints: QueryConstraints, use_llm: bool) -> ResolvedProfile:
    """The category profile: books, capabilities and who owns them (one lookup).

    Keyed by the product type; the search phrase may name an older record (``resolve_profile``);
    a product type seen first gets the Jev classifier's capabilities (``ensure_profile``).
    """
    phrase = constraints.search_phrase
    category = constraints.product_type.strip() or phrase
    with stage("knowledge resolve"):
        profile = await ensure_profile(category, use_llm=use_llm, search_phrase=phrase)
    if profile.brand_book is None and profile.model_book is None:
        logger.info(
            "no brand knowledge for %r — run `buysmarter learn-category %r` to generate it",
            category,
            category,
        )
    return profile


@dataclass(frozen=True)
class _Run:
    """One run's settled request: what every stage after the parse reads."""

    query: str
    constraints: QueryConstraints
    profile: ResolvedProfile
    capabilities: ActiveCapabilities
    use_llm: bool


@dataclass
class _Pool:
    """The collected goods: the deduped new pool, its cross-source notes, the OLX ads."""

    deduped: list[Candidate]
    notes: dict[tuple[str, str], CrossSourceNote]
    used: list[Candidate]
    persist_rows: list[ProductRow]


@dataclass(frozen=True)
class Clarify:
    """The caller's side of a clarification: its answer, the round, whether to ask at all."""

    answer: str | None = None
    round: int = 0
    no_clarify: bool = False


async def understand_request(
    query: str, parsed: QueryConstraints | None, use_llm: bool, ask: Clarify
) -> tuple[str, QueryConstraints, str | None]:
    """Parse (or reuse ``parsed``), take the answer, flag restricted goods, settle questions.

    Returns the query (with the user's own answer added), the constraints and the option
    assumed on the last round. Raises ``NeedsClarification`` or ``NothingToSearch``.
    """
    with stage("query parse"):
        base = parsed if parsed is not None else await _parse(query, use_llm)
        answer = (ask.answer or "").strip()
        if answer and (chosen := apply_answer(base, answer)) is not None:
            base = chosen
        elif answer:
            query = f"{query} {answer}"
            base = await _parse(query, use_llm)
    if base.restricted is None and (kind := restricted_kind(query)):
        base = base.model_copy(update={"restricted": kind})
    base, assumed = settle_request(base, clarify_round=ask.round, no_clarify=ask.no_clarify)
    return query, base, assumed


async def _constraints(
    query: str,
    base_constraints: QueryConstraints,
    city: str | None,
    budget: int | None,
    trusted_only: bool,
    use_llm: bool,
) -> tuple[QueryConstraints, bool]:
    """Resolve the city, let capabilities read the query, apply the caller's overrides."""
    resolved_city, city_defaulted = await resolve_city(base_constraints.city, city)
    constraints = read_query(REGISTRY, base_constraints, query=query, use_llm=use_llm)
    return _apply_overrides(constraints, resolved_city, budget, trusted_only), city_defaulted


async def _collect(run: _Run, active: dict[str, ProductSource]) -> _Pool:
    """Sources → merge → official stores → history → relevance → dedupe; OLX ads filtered."""
    constraints, capabilities = run.constraints, run.capabilities
    book, model_book = run.profile.brand_book, run.profile.model_book
    with stage("collect"):
        collected = await gather_sources(
            active,
            constraints.search_phrase,
            constraints.city,
            book,
            constraints.budget_max,
            capabilities.searches(),
        )
    new_candidates, persist_rows = merge_new_goods(collected)
    with stage("official stores"):
        new_candidates += await official_candidates(book, constraints, capabilities.reject_listing)
    # Attach 30-day price history (from prior runs' snapshots) before ranking
    # so the price axis can reward a genuine dip; best-effort, no-op keyless.
    await attach_price_history(new_candidates)
    with stage("relevance filter"):
        # independent LLM passes (new goods, OLX ads): one wait instead of two
        new_candidates, used = await asyncio.gather(
            _filter_relevant_candidates(
                new_candidates,
                constraints.search_phrase,
                run.use_llm,
                capabilities.protected(new_candidates),
            ),
            _filter_used(
                cast(list[Candidate], collected.get("olx") or []),
                constraints,
                run.use_llm,
                capabilities.reject_listing,
            ),
        )
    deduped, notes = dedupe_cross_source(new_candidates, model_book)
    deduped = attach_reference_prices(deduped, book, model_book)
    return _Pool(deduped, notes, used, persist_rows)


async def _rank(
    run: _Run, pool: _Pool, active: dict[str, ProductSource], with_review_summary: bool
) -> tuple[Recommendation, ReviewSummary | None, str | None]:
    """Capabilities enrich the pool, ``rank``, then the review pass (sources still open)."""
    await run.capabilities.enrich(pool.deduped)
    with stage("ranking"):
        recommendation = rank(
            pool.deduped, run.constraints, query=run.query, hooks=run.capabilities.rank_hooks()
        )
    # Second pass: fetch reviews for the top Kaspi picks, discount fake-looking review
    # axes, re-sort, then summarize the (possibly changed) best pick from its own source.
    with stage("review summaries"):
        return await review_pass(
            recommendation,
            active,
            run.use_llm,
            with_review_summary,
            run.constraints.required_features,
        )


async def _used_listings(
    run: _Run, pool: _Pool, recommendation: Recommendation | None
) -> list[UsedListing]:
    """The OLX section: against the ranking, or, with no new pick, against the rejected pool."""
    profile = run.profile
    knowledge = (profile.brand_book, profile.model_book, run.use_llm, profile.model_book_owner)
    if recommendation is None:
        return await used_market_without_pick(pool.deduped, pool.used, run.constraints, *knowledge)
    return await used_market(recommendation, pool.used, run.constraints, *knowledge)


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
    answer: str | None = None,
    clarify_round: int = 0,
    no_clarify: bool = False,
) -> RecommendResult:
    """Run the full multi-source recommend pipeline.

    ``sources`` selects the marketplaces to query (defaults to Kaspi only for
    single-source callers; the CLI passes all three). ``source`` / ``ozon_source``
    / ``olx_source`` inject adapters (used in tests). Kaspi + Ozon feed the
    ranking pool; OLX contributes the advisory used-market section only.
    ``parsed`` lets a caller reuse a constraints parse it already did (the CLI
    parses once to decide whether to prompt for a budget, then passes the
    result back in here instead of parsing the query a second time).

    Before any search the request may need a clarification (``NeedsClarification``):
    the caller asks the user and calls again with ``answer`` and ``clarify_round + 1``;
    ``no_clarify`` searches the first option instead (``services.understanding``).
    """
    total_start = time.perf_counter()
    skipped_llm_steps = list(NO_LLM_STEPS) if use_llm and _no_llm_route() else []
    use_llm = use_llm and not skipped_llm_steps
    ask = Clarify(answer, clarify_round, no_clarify)
    query, understood, assumed = await understand_request(query, parsed, use_llm, ask)
    constraints, city_defaulted = await _constraints(
        query, understood, city, budget, trusted_only, use_llm
    )
    profile = await _resolve_knowledge(constraints, use_llm)
    capabilities = activate(REGISTRY, profile, constraints, query=query, use_llm=use_llm)
    ask_capability(capabilities.question(), constraints, ask.round, ask.no_clarify)
    requested = sources or ["kaspi"]
    # off the loop: picking Ozon's browser may launch one to probe it
    active = await asyncio.to_thread(resolve_sources, requested, source, ozon_source, olx_source)
    run = _Run(query, constraints, profile, capabilities, use_llm)
    try:
        pool = await _collect(run, active)
        recommendation, review_summary, review_summary_note = await _rank(
            run, pool, active, with_review_summary
        )
    except NoCandidatesPassed as exc:
        # rank() is the only raiser, so the pool and the OLX ads are already collected
        raise NoNewPick(exc.rejected_reasons, await _used_listings(run, pool, None)) from exc
    finally:
        for src in active.values():
            await aclose(src)

    if persist and pool.persist_rows:
        await save_snapshots(pool.persist_rows, constraints.search_phrase)
    apply_cross_notes(recommendation, pool.notes)
    used_listings = await _used_listings(run, pool, recommendation)
    logger.info("stage %s took %.2fs", "total", time.perf_counter() - total_start)
    return RecommendResult(
        recommendation=recommendation,
        constraints=constraints,
        used_listings=used_listings,
        city_defaulted=city_defaulted,
        review_summary=review_summary,
        review_summary_note=review_summary_note,
        knowledge_missing=profile.brand_book is None and profile.model_book is None,
        skipped_llm_steps=skipped_llm_steps,
        unavailable_sources=[name for name in requested if name not in active],
        ingredients_unchecked=capabilities.flags(recommendation).get(INGREDIENTS_UNCHECKED, False),
        restricted_note=restricted_note(constraints.restricted),
        assumed=assumed,
        capabilities=capability_states(REGISTRY, profile, constraints),
        profile_found=profile.found,
    )


def _keep_listing(text: str) -> str | None:
    return None


async def _filter_used(
    used: list[Candidate],
    constraints: QueryConstraints,
    use_llm: bool,
    reject: Callable[[str], str | None] = _keep_listing,
) -> list[Candidate]:
    """Drop OLX ads the capabilities reject (another audience) or off the category.

    No fail-open floor here: an empty OLX section is fine, a wrong one is not.
    """
    used = [u for u in used if reject(u.title) is None]
    if not used:
        return used
    relevant = await _filter_relevant_candidates(
        used, constraints.search_phrase, use_llm, min_keep=0, keep_threshold=USED_KEEP_THRESHOLD
    )
    return _drop_bait_prices(relevant)


# An OLX ad far below the other ads for the same query is bait or a scam ("СРОЧНО! iPhone 13"
# at 35 000 next to 125 000). The median needs a few ads to mean anything.
# ponytail: one iPhone run; retune at the first real ad dropped or bait ad kept.
_BAIT_PRICE_RATIO = 0.3
_BAIT_MIN_ADS = 5


def _drop_bait_prices(used: list[Candidate]) -> list[Candidate]:
    """Drop ads priced under ``_BAIT_PRICE_RATIO`` of the median ad; logged for retuning."""
    prices = [u.price for u in used if u.price > 0]
    if len(prices) < _BAIT_MIN_ADS:
        return used
    floor = statistics.median(prices) * _BAIT_PRICE_RATIO
    kept = []
    for ad in used:
        if 0 < ad.price < floor:
            logger.info("olx: drop %r at %d, under %d (bait price)", ad.title, ad.price, floor)
        else:
            kept.append(ad)
    return kept


def _apply_overrides(
    constraints: QueryConstraints,
    city: str | None,
    budget: int | None,
    trusted_only: bool,
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
# product, and the used market is OLX's job, which always runs. It sets ``condition``.
_CONDITION_WORDS = {
    "б/у": "used",
    "бу": "used",
    "б.у.": "used",
    "used": "used",
    "новый": "new",
    "новое": "new",
    "new": "new",
}


def _word_tokens(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"\w+", text)]


def _drop_category_implied_features(constraints: QueryConstraints) -> QueryConstraints:
    """Drop required features already covered by the category, and condition words.

    The LLM parser turns "TWS наушники" into search_phrase="TWS наушники" +
    required_features=["TWS"] — but a category-defining jargon token is not a
    spec to string-match: Kaspi titles the same product "Наушники Huawei
    Freebuds SE 4" with no "TWS" anywhere, so the hard filter would reject
    exactly the on-category branded candidates recall just found (live-run
    regression). Category membership is the relevance filter's job; only
    features that add information beyond the category (IPX7, ANC...) remain
    hard requirements. A multi-word feature counts as covered when all its words
    are in the category ("iPhone 13" in "смартфон iPhone 13").
    """
    category_tokens = set(_word_tokens(constraints.search_phrase))
    words = [f.strip().lower() for f in constraints.required_features]
    kept = [
        f
        for f, word in zip(constraints.required_features, words, strict=True)
        if word not in _CONDITION_WORDS and not set(_word_tokens(f)) <= category_tokens
    ]
    if len(kept) == len(constraints.required_features):
        return constraints
    updates: dict[str, Any] = {"required_features": kept}
    conditions = [_CONDITION_WORDS[w] for w in words if w in _CONDITION_WORDS]
    if constraints.condition is None and conditions:
        updates["condition"] = conditions[0]
    return constraints.model_copy(update=updates)


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
    candidates: list[Candidate],
    category: str,
    keep_threshold: float = KEEP_THRESHOLD,
    fail_open: bool = True,
) -> tuple[list[Candidate], list[Drop]]:
    """LLM pass: classify surviving candidates, split into kept + dropped.

    A title naming the queried model ("iPhone 13" for "смартфон iPhone 13") skips it: Jev scores
    such titles 0.2-0.6 from call to call and dropped real ads.
    # ponytail: an accessory naming the model ("Стекло iPhone 13") the marker pre-pass missed now
    # stays; send named titles to Jev with a lower threshold if one shows up in a report.
    """
    named = [c for c in candidates if names_query_model(category, c.title)]
    rest = [c for c in candidates if not names_query_model(category, c.title)]
    flags = await filter_relevant(
        category, [c.title for c in rest], keep_threshold, fail_open=fail_open
    )
    kept = named + [c for c, keep in zip(rest, flags, strict=True) if keep]
    dropped = [(c, _LLM_DROP_REASON) for c, keep in zip(rest, flags, strict=True) if not keep]
    for cand, _ in dropped:
        logger.info("relevance: drop %r (%s)", cand.title, _LLM_DROP_REASON)
    return kept, dropped


def _restore_min(
    kept: list[Candidate], dropped: list[Drop], min_keep: int = _MIN_RELEVANT_CANDIDATES
) -> tuple[list[Candidate], list[Candidate]]:
    """Fail-open guard: never let the pool fall below the minimum.

    If filtering left fewer than the floor, the highest-rated dropped candidates
    are added back until the floor is met. Returns ``(pool, restored)``.
    """
    if len(kept) >= min_keep or not dropped:
        return kept, []
    need = min_keep - len(kept)
    best_first = sorted(
        (c for c, _ in dropped),
        key=lambda c: (c.rating or 0.0, c.review_count or 0),
        reverse=True,
    )
    restored = best_first[:need]
    return kept + restored, restored


def _log_drop_summary(dropped: list[Drop], restored: list[Candidate], kept_count: int) -> None:
    """Log how many candidates were dropped and why (count per reason), and each one restored."""
    if not dropped:
        return
    for cand in restored:
        logger.info("relevance: restore %r (to meet the floor)", cand.title)
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
    min_keep: int = _MIN_RELEVANT_CANDIDATES,
    keep_threshold: float = KEEP_THRESHOLD,
) -> list[Candidate]:
    """Drop accessories/non-products before ranking; never below the floor.

    Heuristic always runs; the LLM pass runs only with ``use_llm`` and a pool
    still larger than the floor (nothing to gain otherwise). Fail-open with a floor;
    without one (OLX ads) a classifier outage keeps only titles naming the model.
    Protected candidates count towards the floor.

    ``protected_ids`` (source_product_ids of substance-confirmed candidates in
    composition-driven categories) bypass both passes: an adapalene gel proven
    to carry the requested substance must not be evicted by a "крем vs гель"
    form-factor relevance drop — that is exactly what defeated recall before.
    """
    protected_ids = protected_ids or set()
    protected = [c for c in candidates if c.source_product_id in protected_ids]
    rest = [c for c in candidates if c.source_product_id not in protected_ids]
    kept, dropped = _accessory_drops(rest, category)
    floor = max(0, min_keep - len(protected))
    if use_llm and len(kept) > floor:
        kept, llm_dropped = await _llm_drops(kept, category, keep_threshold, fail_open=min_keep > 0)
        dropped.extend(llm_dropped)
    pool, restored = _restore_min(kept, dropped, floor)
    _log_drop_summary(dropped, restored, len(pool))
    return protected + pool
