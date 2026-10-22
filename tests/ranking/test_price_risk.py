"""Counterfeit risk from the gap to the genuine item's price, and the 2026-10-22 sneaker run."""

from buysmarter.ranking.engine import rank
from buysmarter.ranking.flags import SUSPICIOUS_PRICE_CON
from buysmarter.ranking.models import Candidate, MarketContext, QueryConstraints
from buysmarter.ranking.price_risk import Risk, price_risk
from buysmarter.ranking.selection import is_barred

_MARKET = MarketContext(
    min_price=2_695,
    median_price=19_000,
    trusted_median_price=22_990,
    tiered_pool_size=5,
    max_review_count=702,
    pool_size=10,
)


def _sneaker(pid: str, title: str, price: int, **overrides) -> Candidate:
    defaults = dict(
        source="kaspi",
        source_product_id=pid,
        title=title,
        price=price,
        rating=4.9,
        review_count=30,
        url=f"https://kaspi.kz/shop/p/-{pid}/",
    )
    defaults.update(overrides)
    return Candidate(**defaults)


_FAKE_ADIDAS = _sneaker(
    "1", "Adidas Yeezy", 2_695, brand="Adidas", brand_tier="A", reference_price=21_990
)


def test_a_known_brand_far_below_its_genuine_price_is_suspicious():
    assert price_risk(_FAKE_ADIDAS, _MARKET)[0] is Risk.SUSPICIOUS


def test_a_moderate_gap_is_low_risk_not_a_fake():
    sale = _FAKE_ADIDAS.model_copy(update={"price": 13_000})

    assert price_risk(sale, _MARKET)[0] is Risk.LOW


def test_a_small_gap_is_a_normal_deal():
    sale = _FAKE_ADIDAS.model_copy(update={"price": 19_000})

    assert price_risk(sale, _MARKET)[0] is Risk.NONE


def test_official_sellers_and_used_markets_carry_no_price_risk():
    official = _FAKE_ADIDAS.model_copy(update={"official_seller": True})
    used = _FAKE_ADIDAS.model_copy(update={"source": "olx"})

    assert price_risk(official, _MARKET)[0] is Risk.NONE
    assert price_risk(used, _MARKET)[0] is Risk.NONE


def test_without_knowledge_only_a_deep_gap_to_the_trusted_median_counts():
    cheap = _FAKE_ADIDAS.model_copy(update={"reference_price": None, "price": 5_000})
    budget_brand = cheap.model_copy(update={"price": 11_500})

    assert price_risk(cheap, _MARKET)[0] is Risk.SUSPICIOUS
    assert price_risk(budget_brand, _MARKET)[0] is Risk.NONE


def test_sneaker_run_regression_genuine_puma_beats_the_fakes():
    pool = [
        _sneaker(
            "15358806",
            "Кроссовки Мужские кроссовки PODPODOSHVOI yeezy черный",
            2_695,
            review_count=323,
            mimic_brand="Adidas",
        ),
        _sneaker(
            "24573262",
            "Кроссовки XAN Air Jordan 4 черный",
            17_500,
            brand="XAN",
            mimic_brand="Nike",
        ),
        _sneaker(
            "20391612",
            "Кроссовки Puma Кроссовки Anzarun 2 Lite SLIPTECH",
            19_200,
            brand="Puma",
            brand_tier="B",
            official_seller=True,
            reference_price=13_000,
        ),
        _sneaker(
            "11931521",
            "Кроссовки adidas ORIGINALS CAMPUS 00s черный",
            32_990,
            brand="Adidas",
            brand_tier="A",
            reference_price=21_990,
        ),
        _sneaker(
            "99",
            "Кроссовки Adidas Yeezy 350",
            3_500,
            brand="Adidas",
            brand_tier="A",
            reference_price=21_990,
        ),
    ]

    result = rank(pool, QueryConstraints(search_phrase="мужские кроссовки", budget_max=35_000))

    assert result.best.candidate.source_product_id in {"20391612", "11931521"}
    winners = [result.best, *result.alternatives.values()]
    assert all(s.candidate.source_product_id not in {"15358806", "24573262", "99"} for s in winners)
    by_id = {s.candidate.source_product_id: s for s in result.ranked}
    assert SUSPICIOUS_PRICE_CON in by_id["99"].cons
    assert all(is_barred(by_id[pid]) for pid in ("15358806", "24573262", "99"))


def test_an_unnamed_listing_takes_no_role_while_named_products_compete():
    pool = [
        _sneaker("1", "Кроссовки Puma Smash 3.0", 22_000, brand="Puma", brand_tier="B"),
        _sneaker("2", "Кроссовки мужские на осень", 13_506, source="ozon"),
        _sneaker("3", "Кроссовки Anta", 25_890, brand="Anta", brand_tier="B"),
    ]

    result = rank(pool, QueryConstraints(search_phrase="мужские кроссовки"))

    roles = [result.best, *result.alternatives.values()]
    assert all(s.candidate.source_product_id != "2" for s in roles)
