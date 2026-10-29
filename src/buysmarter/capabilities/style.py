"""Style: clothing ranked by the user's own taste (plan 2026-10-27-clothing-taste-mvp, В2–В6).

A category capability Jev offers for clothing, footwear and accessories. It works only with
a style profile (``style.profile``): its brands bring up to six extra Kaspi searches, those
the category's brand book knows first and those only other categories' books know last.
With sample photos or phrases (and the ``style`` extra, its model downloaded) every card's
main photo is compared with them for the «Стиль» axis. The axis is the card's percentile in
the pool by its nearest liked sample, less ``NEVER_PER_Z`` for every standard deviation a
«нет» sample is closer than the pool's average; a card without a photo, or without a liked
sample of its garment (``style.match``), gets half.
The user's strength (0–50, default 20) is the axis weight; 0 switches it off.
"""

import asyncio
import bisect
import functools
import logging
import statistics
from typing import Any

import httpx

from buysmarter import style
from buysmarter.capabilities.base import Capability, CapabilityContext
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.profile import BRAND_KEY, CapabilitySetting, ResolvedProfile
from buysmarter.knowledge.store import stored_records
from buysmarter.ranking.models import AxisScore, Candidate, QueryConstraints
from buysmarter.services.collect import stage
from buysmarter.style import profile
from buysmarter.style import weights as model_files
from buysmarter.style.profile import LIKE_PHOTO, LIKE_TEXT, NEVER_PHOTO, NEVER_TEXT

logger = logging.getLogger(__name__)

STYLE = "style"
NEVER_PER_Z = 0.15  # the spike's never-phrase penalty, per z-unit above the pool mean
MAX_BRAND_SEARCHES = 6
ENRICH_TIMEOUT_S = 30.0  # ~100 new photos take ~8 s; a slow CDN must not hold the search
NO_PHOTO = "нет фото — не сравнивали"
NO_SAMPLE_OF_TYPE = "нет образцов такого типа"
_UNLIKE = "не похоже на ваши образцы"
_LIKES = {LIKE_PHOTO: "похоже на ваше фото «{}»", LIKE_TEXT: "похоже на «{}»"}

# per compared kind: the pool's sorted cosines (likes) or (mean, deviation) (nevers)
_PoolState = dict[str, Any]


class Style(Capability):
    """The user's brands searched, the «Стиль» axis from their sample photos and phrases."""

    name = STYLE
    title = "Стиль"
    axis_name = STYLE
    axis_weight = float(profile.STRENGTH_DEFAULT)
    jev_question = (
        "Is it clothing, footwear or a fashion accessory, where looks and personal style "
        "matter when choosing?"
    )

    _elsewhere: frozenset[str] = frozenset()  # the user's brands some stored brand book knows

    async def prime(self) -> None:
        try:
            current = await profile.load()
        except Exception as exc:  # noqa: BLE001 - no profile is a search without style
            logger.warning("Style profile not loaded (%r); style off for this search", exc)
            return
        books = [
            BrandBook.model_validate(raw[BRAND_KEY])
            for raw in (await stored_records()).values()
            if raw.get(BRAND_KEY)
        ]
        self._elsewhere = frozenset(
            b for b in current.brands if any(book.resolve(b)[0] for book in books)
        )

    def current_axis_weight(self) -> float:
        """The user's strength; 0 with brands only (nothing to compare photos with)."""
        current = profile.current()
        return float(current.strength) if current.samples else 0.0

    def setting(
        self, resolved: ResolvedProfile, constraints: QueryConstraints
    ) -> CapabilitySetting | None:
        """On for the category, and the user has a profile with a strength above 0."""
        current = profile.current()
        if current.empty or current.strength <= 0:
            return None
        return resolved.enabled(STYLE)

    def searches(self, ctx: CapabilityContext) -> list[tuple[str, list[str]]]:
        product = ctx.constraints.product_type.strip() or ctx.constraints.search_phrase
        book = ctx.profile.brand_book

        def order(brand: str) -> int:
            """This category's brands, then unknown ones, then other categories' (sneakers)."""
            if book is not None and book.resolve(brand)[0]:
                return 0
            return 2 if brand in self._elsewhere else 1

        brands = sorted(profile.current().brands, key=order)[:MAX_BRAND_SEARCHES]
        return [(f"{product} {brand}", [brand]) for brand in brands]

    async def enrich(self, ctx: CapabilityContext, pool: list[Candidate]) -> None:
        current = profile.current()
        if not current.samples or not style.available() or model_files.missing():
            return  # brands only, or no model: the axis gives every card half
        with stage("style"):
            try:
                await asyncio.wait_for(_match(pool, current), ENRICH_TIMEOUT_S)
            except TimeoutError:
                logger.warning("Style photos took over %.0f s; the rest unjudged", ENRICH_TIMEOUT_S)

    def prepare(self, ctx: CapabilityContext, passed: list[Candidate]) -> _PoolState:
        kinds = {kind for c in passed for kind in c.style_match}
        return {
            kind: _pool_stats(kind, [c for c in passed if kind in c.style_match]) for kind in kinds
        }

    def axis(
        self,
        ctx: CapabilityContext,
        state: _PoolState,
        candidate: Candidate,
        weights: dict[str, float],
    ) -> AxisScore | None:
        if not profile.current().samples:
            return None  # brands only: no axis
        weight = weights.get(STYLE, 0.0)
        ratio, explanation = _judged(state, candidate.style_match)
        return AxisScore(
            name=STYLE, score=round(weight * ratio, 2), max_score=weight, explanation=explanation
        )


async def _match(pool: list[Candidate], current: profile.Profile) -> None:
    from buysmarter.style import match  # the extra: only once it is known to be there
    from buysmarter.style.encoder import shared

    model = await asyncio.to_thread(shared)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        await match.match_pool(pool, current, model, functools.partial(match.fetch_photo, client))


def _judged(state: _PoolState, match: dict[str, tuple[float, str]]) -> tuple[float, str]:
    """The axis share (0-1) and why: likeness less the «нет» penalty; half without a photo."""
    if not match:
        return 0.5, NO_PHOTO
    like, liked = _liked(state, match)
    penalty, never = _never(state, match)
    parts = [liked if like >= 0.5 else _UNLIKE]
    if penalty > 0:
        parts.append(f"похоже на «нет: {never}»")
    return min(max(like - penalty, 0.0), 1.0), "; ".join(parts)


def _pool_stats(kind: str, cards: list[Candidate]) -> Any:
    values = [c.style_match[kind][0] for c in cards]
    if kind in _LIKES:
        return sorted(values)
    return statistics.fmean(values), statistics.pstdev(values)


def _liked(state: _PoolState, match: dict[str, tuple[float, str]]) -> tuple[float, str]:
    """The best percentile among the liked kinds, with its explanation; half when none."""
    found = [
        (_percentile(state[kind], match[kind][0]), _LIKES[kind].format(match[kind][1]))
        for kind in _LIKES
        if kind in match
    ]
    return max(found) if found else (0.5, NO_SAMPLE_OF_TYPE)


def _percentile(pool: list[float], value: float) -> float:
    """The share of the other cards below ``value`` (0 lowest, 1 highest); ties share a rank."""
    if len(pool) < 2:
        return 0.5
    rank = (bisect.bisect_left(pool, value) + bisect.bisect_right(pool, value) - 1) / 2
    return rank / (len(pool) - 1)


def _never(state: _PoolState, match: dict[str, tuple[float, str]]) -> tuple[float, str]:
    """The largest «нет» penalty and the sample behind it; ``(0, "")`` when none."""
    found = [(0.0, "")]
    for kind in (NEVER_PHOTO, NEVER_TEXT):
        if kind in match:
            mean, deviation = state[kind]
            z = (match[kind][0] - mean) / deviation if deviation > 0 else 0.0
            found.append((NEVER_PER_Z * max(z, 0.0), match[kind][1]))
    return max(found)
