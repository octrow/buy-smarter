"""Pydantic v2 models for the ranking engine.

Pure data — no DB, no LLM, no network. ``Candidate`` is the input shape the
engine consumes (already normalized/deduped upstream); everything else is
engine output.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal["product", "need", "open", "multi", "service", "unclear"]
Restricted = Literal["rx", "alcohol", "tobacco", "weapon"]


class QueryConstraints(BaseModel):
    """Hard + soft constraints extracted from a user request (CONTEXT.md "Request intent").

    ``search_phrase`` is what the marketplaces get (and what substances are read from),
    ``product_type`` the category profile key without audience, size or theme.
    """

    search_phrase: str = ""
    product_type: str = ""
    intent: Intent = "product"
    # The problem a ``need`` request names ("перхоть"), passed on to capabilities.
    need: str | None = None
    # Products to choose between: a ``multi`` request's items, a need's product types.
    options: list[str] = Field(default_factory=list)
    restricted: Restricted | None = None
    city: str | None = None
    budget_max: int | None = None
    # Shown only in phase 2 (not a filter).
    budget_min: int | None = None
    # Why a budget named in the query was not used ("бюджет в другой валюте не учтён").
    budget_note: str | None = None
    # "used" or "new" when the query says so; not a feature.
    condition: str | None = None
    required_features: list[str] = Field(default_factory=list)
    excluded_brands: list[str] = Field(default_factory=list)
    preferred_marketplaces: list[str] = Field(default_factory=list)
    usage_notes: str | None = None
    trusted_brands_only: bool = False
    # Who the product is for, from the query ("мужские кроссовки" -> "male"):
    # a listing marked for another audience is rejected (``filters``). None == any.
    gender: str | None = None
    # Clothing/shoe size the user asked for ("42", "XL"). Marketplace cards do not
    # say which sizes are in stock, so it is reported as unverified, never filtered.
    size: str | None = None


class PriceHistory(BaseModel):
    """Aggregated price snapshots over a recent window (price-trend signal).

    ``avg``/``min`` are over the past N days of ``PriceSnapshot.min_price`` for
    this product; ``count`` is how many snapshots backed the aggregate. Attached
    best-effort from the DB before ranking, so it is ``None`` without Postgres.
    """

    avg: float
    min: int
    count: int


class Candidate(BaseModel):
    """A single normalized product offer, ready to be ranked."""

    source: str
    source_product_id: str
    title: str
    brand: str | None = None
    brand_tier: str | None = None
    price: int
    rating: float | None = None
    review_count: int | None = None
    specs: dict[str, str] = Field(default_factory=dict)
    availability: bool = True
    url: str | None = None
    price_history: PriceHistory | None = None
    # Reviews gained per week vs a snapshot >= 7 days old (ranking.velocity); None without DB.
    review_velocity: float | None = None
    # How many distinct seller offers back this listing. On Kaspi a single card
    # aggregates dozens of sellers (shown price = the cheapest one), so a review
    # complaint may indict one seller, not the product — the review-summary
    # counterfeit penalty softens on multi-seller cards. 0 == not fetched /
    # single buy-box (e.g. Ozon), treated as directly attributable.
    offer_count: int = 0
    # Display-only recommended seller on multi-seller cards (not scored).
    best_seller: str | None = None
    # Composition-driven categories (docs/PLAN.md): canonical active substances
    # detected in this product (via ``llm.ingredients``) and its concentration, if
    # any. Empty/None for spec-driven categories — the ingredient axis is skipped.
    detected_substances: list[str] = Field(default_factory=list)
    concentration: str | None = None
    # The composition got an answer (extraction, or the title names the substance).
    # False == not checked: a missing substance is then unknown, never proven absent.
    ingredients_checked: bool = False
    # Known brand this title mimics without genuinely matching (counterfeit signal).
    mimic_brand: str | None = None
    # Pricier duplicate price / this price, when a same-product dedupe found a
    # big gap elsewhere (anti-counterfeit signal, brand tier irrelevant).
    dup_price_gap: float | None = None
    # Delivery of the best offer (the one the price comes from): ISO date in
    # Asia/Almaty and fee in KZT. None == unknown (e.g. Ozon, no offers fetched).
    delivery_date: str | None = None
    delivery_cost: float | None = None
    # A performance index for this model (0-100), averaged into the specs axis, and the
    # source that gave it ("Kimovil" for phones). None == no index for this model.
    benchmark_index: float | None = None
    benchmark_label: str | None = None
    # The brand was read from the title only (the card had no brand field): a
    # weaker signal than a brand field, since counterfeits name a brand in the title.
    brand_from_title: bool = False
    # Lowest price of a genuine item (KZT) to compare this listing against: the
    # model's official price when the model is known, else the brand's cheapest
    # genuine item in the category (official stores). None == no knowledge.
    reference_price: int | None = None
    reference_kind: str | None = None  # "model" | "brand"
    # Sold by the brand itself or its official distributor/partner.
    official_seller: bool = False
    # The marketplace's own category path ("Обувь > Мужская обувь > ..."), if any.
    category_path: list[str] = Field(default_factory=list)


class AxisScore(BaseModel):
    """One explainable scoring axis (price, reviews, specs, ...)."""

    name: str
    score: float
    max_score: float
    explanation: str


class ScoredCandidate(BaseModel):
    """A candidate with its total score, confidence, and per-axis breakdown."""

    candidate: Candidate
    total_score: float
    confidence: float
    axes: list[AxisScore]
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    # Points subtracted from ``total_score`` by the review-summary penalty loop
    # (``ranking.apply_summary_penalty``). Shown as a separate "Penalty" line
    # near the score; the axis breakdown itself is left untouched. 0.0 == none.
    review_penalty: float = 0.0


class MarketContext(BaseModel):
    """Pool-wide stats over the candidates passing hard filters.

    Computed once per ranking run and passed into ``score`` so every
    candidate is scored relative to the same market snapshot.
    """

    min_price: int
    median_price: float
    trusted_median_price: int | None
    tiered_pool_size: int = 0  # candidates with a known brand_tier (trusted_median_price's base)
    max_review_count: int
    max_review_count_by_source: dict[str, int] = Field(default_factory=dict)
    pool_size: int


class Recommendation(BaseModel):
    """Final ranking output: best pick + role-labeled alternatives."""

    best: ScoredCandidate
    alternatives: dict[str, ScoredCandidate] = Field(default_factory=dict)
    ranked: list[ScoredCandidate] = Field(default_factory=list)
    rejected_count: int = 0
    # Compact reason-key -> count breakdown of hard-filter rejections (e.g.
    # {"price exceeds budget": 3, "missing required feature(s)": 2}), for a
    # one-line "Отсеяно: ..." CLI/report summary. Empty when nothing rejected.
    rejected_reasons: dict[str, int] = Field(default_factory=dict)


# --- What a caller adds to rank(): weights, checks and scoring ------------------
# Plain functions over candidates; ranking stays pure.

AxisFn = Callable[[Candidate, dict[str, float]], AxisScore | None]
NotesFn = Callable[[Candidate, list[str], list[str]], tuple[list[str], list[str]]]


@dataclass(frozen=True)
class PoolScoring:
    """One extra scoring, prepared over the candidates that passed the filters."""

    axis: AxisFn | None = None
    notes: NotesFn | None = None


@dataclass(frozen=True)
class RankHooks:
    """Weights, extra rejection checks and scoring a caller adds to ``rank``.

    ``reject_listing`` predicates run after the core's listing checks and get the listing
    text (title and category path); ``hard_features`` adjust the features string-matched;
    ``reject_content`` runs after the feature check; each ``scoring`` builder sees the
    passed pool and returns an axis (scored before the base axes) and/or pros-cons notes.
    """

    weights: dict[str, float] | None = None
    reject_listing: tuple[Callable[[str], str | None], ...] = ()
    hard_features: tuple[Callable[[list[str]], list[str]], ...] = ()
    reject_content: tuple[Callable[[Candidate], str | None], ...] = ()
    scoring: tuple[Callable[[list[Candidate]], PoolScoring], ...] = ()
