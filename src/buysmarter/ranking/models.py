"""Pydantic v2 models for the ranking engine.

Pure data — no DB, no LLM, no network. ``Candidate`` is the input shape the
engine consumes (already normalized/deduped upstream); everything else is
engine output.
"""

from pydantic import BaseModel, Field


class QueryConstraints(BaseModel):
    """Hard + soft constraints extracted from a user request."""

    category: str
    city: str | None = None
    budget_max: int | None = None
    required_features: list[str] = Field(default_factory=list)
    excluded_brands: list[str] = Field(default_factory=list)
    preferred_marketplaces: list[str] = Field(default_factory=list)
    usage_notes: str | None = None
    trusted_brands_only: bool = False


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
    # Known brand this title mimics without genuinely matching (counterfeit signal).
    mimic_brand: str | None = None
    # Pricier duplicate price / this price, when a same-product dedupe found a
    # big gap elsewhere (anti-counterfeit signal, brand tier irrelevant).
    dup_price_gap: float | None = None
    # Delivery of the best offer (the one the price comes from): ISO date in
    # Asia/Almaty and fee in KZT. None == unknown (e.g. Ozon, no offers fetched).
    delivery_date: str | None = None
    delivery_cost: float | None = None
    # Smartphones only: Kimovil's overall index (0-100) for this model, averaged into
    # the specs axis (services.phone_specs). None == not a phone or no confident match.
    kimovil_index: float | None = None


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


class EquivalenceGroup(BaseModel):
    """Pool members sharing one active substance (a generic-equivalence group).

    ``cheapest``/``priciest`` identify the extremes by price so the ingredient
    axis can attach the "аналог по действующему веществу" pros (docs/PLAN.md).
    Identities are ``(source, source_product_id)`` pairs.
    """

    cheapest_id: tuple[str, str]
    cheapest_name: str
    cheapest_price: int
    priciest_id: tuple[str, str]
    priciest_name: str
    priciest_price: int
    size: int


class IngredientContext(BaseModel):
    """Pool-wide active-substance context for the composition-driven ingredient axis.

    Computed once per ranking run (like ``MarketContext``). ``required_substances``
    are the canonical substances the query targets; ``groups`` maps a substance to
    its in-pool equivalence group when it has more than one member.
    """

    required_substances: list[str] = Field(default_factory=list)
    groups: dict[str, EquivalenceGroup] = Field(default_factory=dict)


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
