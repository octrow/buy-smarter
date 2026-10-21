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


def test_compatibility_mention_is_not_the_brand_and_mimicry_still_flags():
    # "совместимо с Xiaomi" names the brand the item fits, not its maker, so it
    # gives no tier; the spaced-out "J BL" is still flagged as mimicking JBL.
    product = _product()

    candidate = to_candidate(product, book=_TWS_BRAND_BOOK)

    assert candidate.brand_tier is None
    assert candidate.mimic_brand == "JBL"


_SNEAKER_BOOK = BrandBook(
    tiers={"Adidas": "A", "Nike": "A", "Puma": "B", "Skechers": "B"},
    aliases={"Yeezy": "Adidas", "Jordan": "Nike", "Он": "On", "NB": "New Balance"},
    official_sellers={"Puma": ["PUMA"]},
)


def test_no_brand_card_naming_a_known_line_in_its_title_is_a_mimic_not_that_brand():
    product = _product(
        title="Кроссовки Мужские кроссовки PODPODOSHVOI yeezy черный", brand="Без бренда"
    )

    candidate = to_candidate(product, book=_SNEAKER_BOOK)

    assert candidate.brand is None
    assert candidate.brand_tier is None
    assert candidate.mimic_brand == "Adidas"


def test_unknown_brand_field_with_a_known_brand_in_the_title_is_a_mimic():
    product = _product(title="Кроссовки XAN Air Jordan 4 черный", brand="XAN")

    candidate = to_candidate(product, book=_SNEAKER_BOOK)

    assert (candidate.brand, candidate.brand_tier, candidate.mimic_brand) == ("XAN", None, "Nike")


def test_known_brand_field_resolves_without_mimicry():
    product = _product(title="Кроссовки Puma Кроссовки Anzarun 2 Lite", brand="Puma")

    candidate = to_candidate(product, book=_SNEAKER_BOOK)

    assert (candidate.brand, candidate.brand_tier, candidate.mimic_brand) == ("Puma", "B", None)
    assert candidate.brand_from_title is False


def test_title_is_the_fallback_when_the_card_has_no_brand_field():
    candidate = to_candidate(_product(title="Кроссовки Puma Smash 3.0"), book=_SNEAKER_BOOK)

    assert (candidate.brand, candidate.brand_tier) == ("Puma", "B")
    assert candidate.brand_from_title is True


def test_country_in_maker_field_is_not_evidence_against_the_title_brand():
    candidate = to_candidate(
        _product(title="Кроссовки Puma Smash 3.0"),
        specs={"Производитель": "Китай"},
        book=_SNEAKER_BOOK,
    )

    assert (candidate.brand, candidate.mimic_brand) == ("Puma", None)


def test_word_like_short_aliases_are_dropped_but_latin_brand_marks_stay():
    book = BrandBook(
        tiers={"On": "B", "LG Electronics": "A"},
        aliases={"Он": "On", "on": "On", "LG": "LG Electronics"},
    )

    assert book.aliases == {"LG": "LG Electronics"}
    assert book.resolve("он лёгкий и удобный") == (None, None)
    assert book.resolve("Телевизор LG OLED55") == ("LG Electronics", "A")


def test_a_seller_merely_named_after_the_brand_is_not_official():
    book = BrandBook(tiers={"Nike": "A"}, official_sellers={"Nike": ["Nike"]})

    assert book.is_official_seller("Nike", "NIKE")
    assert not book.is_official_seller("Nike", "Nike Outlet KZ")


def test_offer_from_an_official_seller_marks_the_candidate():
    product = _product(title="Кроссовки Puma Smash 3.0", brand="Puma")

    candidate = to_candidate(
        product, offers=[_offer("PUMA", 22_000, 4.9, 1574)], book=_SNEAKER_BOOK
    )

    assert candidate.official_seller is True


def test_reference_price_prefers_the_model_over_the_brand_floor():
    from buysmarter.knowledge.models_book import ModelBook, ModelEntry
    from buysmarter.normalize.candidate import attach_reference_prices

    book = _SNEAKER_BOOK.model_copy(update={"price_floors": {"Nike": 27_500}})
    models = ModelBook(
        entries=[
            ModelEntry(
                canonical="Air Jordan 4",
                brand="Nike",
                aliases=["air jordan 4"],
                reference_price=100_000,
            )
        ]
    )
    jordan = to_candidate(_product(title="Кроссовки Nike Air Jordan 4", brand="Nike"), book=book)
    basic = to_candidate(_product(title="Кроссовки Nike Revolution", brand="Nike"), book=book)

    with_refs = attach_reference_prices([jordan, basic], book, models)

    assert [(c.reference_price, c.reference_kind) for c in with_refs] == [
        (100_000, "model"),
        (27_500, "brand"),
    ]


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
