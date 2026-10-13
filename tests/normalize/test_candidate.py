"""Tests for raw-source → ``ranking.Candidate`` normalization. No I/O."""

from market_sources.base import RawProduct

from buysmarter.knowledge.brands import BrandBook
from buysmarter.normalize.candidate import to_candidate

_TWS_BRAND_BOOK = BrandBook(tiers={"JBL": "B", "Xiaomi": "C"}, aliases={})


def _product(**overrides) -> RawProduct:
    defaults = dict(
        source="kaspi",
        product_id="1",
        title="J BL Tws300, совместимо с Xiaomi",
        price=2_000,
    )
    defaults.update(overrides)
    return RawProduct(**defaults)


def test_mimicry_still_evaluated_when_a_different_brand_genuinely_resolves():
    # HIGH-3: the title genuinely resolves to Xiaomi ("совместимо с Xiaomi")
    # while separately mimicking JBL via the spaced-out "J BL" — both must be
    # detected: brand_tier resolves Xiaomi, but mimic_brand still flags JBL.
    product = _product()

    candidate = to_candidate(product, book=_TWS_BRAND_BOOK)

    assert candidate.brand == "Xiaomi"
    assert candidate.brand_tier == "C"
    assert candidate.mimic_brand == "JBL"


def test_no_mimic_brand_when_mimicry_matches_the_resolved_brand_itself():
    # A title genuinely resolving to JBL as its own brand must not also be
    # flagged as mimicking JBL (mimicry() already excludes the resolved brand).
    product = _product(title="JBL Tune 510BT")

    candidate = to_candidate(product, book=_TWS_BRAND_BOOK)

    assert candidate.brand == "JBL"
    assert candidate.mimic_brand is None


def test_no_book_means_no_mimic_brand():
    product = _product()
    candidate = to_candidate(product, book=None)
    assert candidate.mimic_brand is None


def _offer(seller, price, rating, reviews, in_stock=True):
    from market_sources.base import RawOffer

    return RawOffer(
        source="kaspi",
        product_id="1",
        seller=seller,
        price=price,
        in_stock=in_stock,
        merchant_rating=rating,
        merchant_review_count=reviews,
    )


def test_best_seller_prefers_high_rated_within_ten_percent():
    offers = [_offer("Дешёвый", 12_000, 4.2, 100), _offer("Магазин X", 13_000, 4.9, 312)]

    c = to_candidate(_product(), offers=offers)

    assert c.best_seller == "Магазин X — 4.9★, 312 отзывов, 13 000 ₸"


def test_best_seller_rejects_low_review_and_too_pricey_and_out_of_stock():
    offers = [
        _offer("Новичок", 12_000, 5.0, 3),
        _offer("Дорогой", 20_000, 5.0, 500),
        _offer("Нет", 12_000, 5.0, 500, in_stock=False),
    ]

    assert to_candidate(_product(), offers=offers).best_seller is None


def test_best_seller_none_without_offers():
    assert to_candidate(_product()).best_seller is None


def test_delivery_comes_from_the_cheapest_offer_the_price_uses():
    from market_sources.base import RawOffer

    pricey = RawOffer(
        source="kaspi",
        product_id="1",
        price=13_000,
        delivery_date="2026-10-13",
        delivery_cost=0.0,
    )
    cheap = RawOffer(
        source="kaspi",
        product_id="1",
        price=12_000,
        delivery_date="2026-10-17",
        delivery_cost=1500.0,
    )

    c = to_candidate(_product(), offers=[pricey, cheap])

    assert c.price == 12_000
    assert c.delivery_date == "2026-10-17"
    assert c.delivery_cost == 1500.0


def test_delivery_is_none_without_offers():
    c = to_candidate(_product())
    assert c.delivery_date is None
    assert c.delivery_cost is None
