"""End-to-end pipeline test with a stubbed source. No network/DB/LLM."""

import json
from pathlib import Path

import pytest
from market_sources.base import RawOffer, RawProduct
from market_sources.kaspi.parse import parse_offers, parse_search, parse_specs

from buysmarter.cli.report import no_new_pick_text
from buysmarter.knowledge.brands import BrandBook
from buysmarter.ranking.models import Candidate, QueryConstraints
from buysmarter.services.collect import (
    _brand_search_plan,
    _brand_targeted_products,
    _collect_kaspi,
    _collect_olx,
    _head_noun,
)
from buysmarter.services.matching import (
    dedupe_cross_source,
    match_used_listings,
    notable_used_listings,
    strip_latin_tokens,
    unbranded_used_listings,
)
from buysmarter.services.recommend import NoNewPick, _filter_used, recommend

SAMPLES_DIR = Path(__file__).parent.parent / "samples" / "kaspi"


def _load(name: str) -> dict:
    return json.loads((SAMPLES_DIR / f"{name}.json").read_text())


class StubKaspiSource:
    """Fake ProductSource, no I/O. Serves per-query results + records queries.

    ``by_query`` maps the exact query string to the cards returned for it,
    enabling tests to prove brand-targeted searches were actually issued
    (and with what queries). ``products`` is the fallback for the main
    category search when no per-query entry matches.
    """

    name = "kaspi"

    def __init__(
        self,
        products: list[RawProduct],
        specs: dict[str, str],
        offers: list[RawOffer],
        by_query: dict[str, list[RawProduct]] | None = None,
    ) -> None:
        self._products = products
        self._specs = specs
        self._offers = offers
        self._by_query = by_query or {}
        self.queries: list[str] = []
        self.specs_requests: list[str] = []

    async def search(self, query: str, city: str | None = None) -> list[RawProduct]:
        self.queries.append(query)
        return self._by_query.get(query, self._products)

    async def specs(self, product_id: str) -> dict[str, str]:
        self.specs_requests.append(product_id)
        return self._specs

    async def offers(self, product_id: str) -> list[RawOffer]:
        return self._offers

    async def reviews(self, product_id: str):  # pragma: no cover - unused here
        return []


def _make_product(product_id: str, title: str, price: float = 20000) -> RawProduct:
    return RawProduct(source="kaspi", product_id=product_id, title=title, price=price)


@pytest.fixture
def stub_source() -> StubKaspiSource:
    products = parse_search(_load("search"))
    specs = parse_specs(_load("specs"))
    offers = parse_offers(_load("offers"), products[0].product_id)
    return StubKaspiSource(products, specs, offers)


async def test_brand_searches_issued_for_known_brands_when_book_exists(
    stub_source: StubKaspiSource,
) -> None:
    await recommend(
        "нужен ирригатор, портативный, до 40000 тг, Алматы",
        use_llm=False,
        source=stub_source,
        persist=False,
    )

    # A/B tier brands + Xiaomi (best-value C tier) get a targeted search each,
    # in addition to the main category search.
    expected_brands = {
        "Waterpik",
        "Philips Sonicare",
        "Oral-B",
        "Burst",
        "Panasonic",
        "B.Well",
        "Revyline",
        "Xiaomi",
    }
    issued_brand_queries = {q for q in stub_source.queries if q != "ирригатор портативный"}
    for brand in expected_brands:
        assert any(q.startswith(brand) for q in issued_brand_queries)
    # Sub-brand aliases of a targeted canonical also get their own search term
    # (e.g. "Mijia" -> Xiaomi) — recall widening, not a mere respelling.
    assert any(q.startswith("Mijia") for q in issued_brand_queries)
    # Noname/other C-tier brands are not worth a dedicated search.
    assert not any(q.startswith("COSLUS") for q in stub_source.queries)
    # Formatting-only respellings of an already-targeted canonical are not
    # worth a separate search (redundant with the canonical's own search).
    assert not any(q.startswith("OralB") or q.startswith("B Well") for q in issued_brand_queries)


async def test_no_brand_searches_when_no_brand_book() -> None:
    stub = StubKaspiSource(products=[_make_product("1", "Kingston SSD 480GB")], specs={}, offers=[])

    await recommend("ssd", use_llm=False, source=stub, persist=False)

    # Only the single main-category search should have been issued.
    assert stub.queries == ["ssd"]


async def test_brand_search_dedupes_against_main_search_by_product_id() -> None:
    main = [
        _make_product("100", "Aulandi ирригатор HF-6 портативный"),
        _make_product("101", "Waterpik WP-100 портативный ирригатор"),
    ]
    # Same product (id "101") also surfaces in the brand-targeted search.
    waterpik_hits = [_make_product("101", "Waterpik WP-100 портативный ирригатор")]

    stub = StubKaspiSource(
        products=main,
        specs={},
        offers=[],
        by_query={"Waterpik ирригатор портативный": waterpik_hits},
    )

    await recommend("ирригатор портативный", use_llm=False, source=stub, persist=False)

    # Product "101" was returned by both the main search and the Waterpik
    # brand search; dedup must fetch its specs only once, not twice.
    assert sorted(stub.specs_requests) == ["100", "101"]


async def test_city_defaults_to_almaty_cyrillic_when_unstated(
    stub_source: StubKaspiSource, monkeypatch
) -> None:
    """No city in the query, no ``--city``, no stored preference -> "Алматы"."""
    from buysmarter.services import recommend as recommend_module

    async def _no_preference() -> str | None:
        return None

    monkeypatch.setattr(recommend_module, "_city_preference", _no_preference)

    result = await recommend(
        "ирригатор портативный до 40000 тг",
        use_llm=False,
        source=stub_source,
        persist=False,
    )

    assert result.constraints.city == "Алматы"
    assert result.city_defaulted is True


async def test_city_not_defaulted_when_query_states_it(stub_source: StubKaspiSource) -> None:
    result = await recommend(
        "нужен ирригатор, портативный, до 40000 тг, Алматы",
        use_llm=False,
        source=stub_source,
        persist=False,
    )
    assert result.constraints.city == "Almaty"
    assert result.city_defaulted is False


@pytest.fixture
def saved_budget(monkeypatch) -> None:
    from buysmarter.services import recommend as recommend_module

    async def _saved() -> int:
        return 50000

    monkeypatch.setattr(recommend_module, "stored_budget", _saved)


async def test_a_saved_budget_applies_when_neither_the_query_nor_the_caller_names_one(
    stub_source: StubKaspiSource, saved_budget
) -> None:
    result = await recommend(
        "ирригатор портативный", use_llm=False, source=stub_source, persist=False
    )
    assert result.constraints.budget_max == 50000


async def test_the_query_and_the_caller_budget_beat_the_saved_one(
    stub_source: StubKaspiSource, saved_budget
) -> None:
    query = "ирригатор портативный до 40000 тг"
    from_query = await recommend(query, use_llm=False, source=stub_source, persist=False)
    from_caller = await recommend(
        query, budget=45000, use_llm=False, source=stub_source, persist=False
    )
    assert from_query.constraints.budget_max == 40000
    assert from_caller.constraints.budget_max == 45000


@pytest.mark.parametrize(("stored", "budget"), [(40000, 40000), ("40 000", 40000), (0, None)])
async def test_stored_budget_reads_a_number_or_older_text_and_zero_is_no_limit(
    monkeypatch, stored, budget
) -> None:
    from buysmarter.services import recommend as recommend_module

    async def _preference(key: str) -> object:
        return stored if key == "budget" else None

    monkeypatch.setattr(recommend_module, "_preference", _preference)
    assert await recommend_module.stored_budget() == budget


async def test_city_override_wins_and_is_not_marked_defaulted(
    stub_source: StubKaspiSource,
) -> None:
    result = await recommend(
        "ирригатор портативный",
        city="Astana",
        use_llm=False,
        source=stub_source,
        persist=False,
    )
    assert result.constraints.city == "Astana"
    assert result.city_defaulted is False


async def test_pipeline_produces_recommendation(stub_source: StubKaspiSource) -> None:
    result = await recommend(
        "нужен ирригатор, портативный, до 40000 тг, Алматы",
        use_llm=False,
        source=stub_source,
        persist=False,
    )

    # Naive parse extracted budget + city, rest became the category.
    assert result.constraints.budget_max == 40000
    assert result.constraints.city == "Almaty"
    assert "ирригатор" in result.constraints.search_phrase

    rec = result.recommendation
    assert rec.best.total_score > 0
    assert rec.best.confidence > 0
    assert rec.best.axes  # explainable breakdown present
    assert rec.alternatives  # role-labeled alternatives present
    # best_premium is correctly omitted here: none of the stub's candidates
    # resolve to a known brand tier (the brand-targeted searches fall back to
    # the same untiered sample products), so there is no trustworthy brand to
    # promote into the "premium" role — see ranking/engine.py _pick_alternatives.
    assert "best_value" in rec.alternatives
    # best_budget only when something undercuts the best pick.
    budget = rec.alternatives.get("best_budget")
    assert budget is None or budget.candidate.price < rec.best.candidate.price


# --- Multi-source pipeline ------------------------------------------------


class FakeSource:
    """Generic in-memory ProductSource. Optionally raises on search."""

    def __init__(
        self,
        name: str,
        products: list[RawProduct],
        specs_map: dict[str, dict[str, str]] | None = None,
        offers_map: dict[str, list[RawOffer]] | None = None,
        fail: bool = False,
        specs_fail_ids: set[str] | None = None,
    ) -> None:
        self.name = name
        self._products = products
        self._specs_map = specs_map or {}
        self._offers_map = offers_map or {}
        self._fail = fail
        self._specs_fail_ids = specs_fail_ids or set()
        self.queries: list[str] = []
        self.specs_requests: list[str] = []

    async def search(self, query: str, city: str | None = None) -> list[RawProduct]:
        if self._fail:
            raise RuntimeError(f"{self.name} exploded")
        self.queries.append(query)
        return self._products

    async def specs(self, product_id: str) -> dict[str, str]:
        self.specs_requests.append(product_id)
        if product_id in self._specs_fail_ids:
            raise RuntimeError(f"404 for product {product_id}")
        return self._specs_map.get(product_id, {})

    async def offers(self, product_id: str) -> list[RawOffer]:
        return self._offers_map.get(product_id, [])

    async def reviews(self, product_id: str):  # pragma: no cover - unused
        return []


def _rp(source: str, pid: str, title: str, price: float) -> RawProduct:
    return RawProduct(source=source, product_id=pid, title=title, price=price)


async def test_multi_source_merge_pools_kaspi_and_ozon() -> None:
    kaspi = FakeSource(
        "kaspi", [_rp("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", 30000)]
    )
    ozon = FakeSource("ozon", [_rp("ozon", "o1", "Revyline RL-100 ирригатор портативный", 18000)])
    olx = FakeSource("olx", [])

    result = await recommend(
        "ирригатор портативный, до 40000 тг",
        use_llm=False,
        sources=["kaspi", "ozon", "olx"],
        source=kaspi,
        ozon_source=ozon,
        olx_source=olx,
        persist=False,
    )

    pool_sources = {result.recommendation.best.candidate.source} | {
        s.candidate.source for s in result.recommendation.alternatives.values()
    }
    # Both marketplaces contributed candidates to the single ranking pool.
    assert pool_sources == {"kaspi", "ozon"}


async def test_without_a_browser_ozon_is_skipped_and_kaspi_and_olx_still_run() -> None:
    # conftest: no browser installed, no saved Ozon session
    kaspi = FakeSource(
        "kaspi", [_rp("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", 30000)]
    )
    olx = FakeSource("olx", [])

    result = await recommend(
        "ирригатор портативный, до 40000 тг",
        use_llm=False,
        sources=["kaspi", "ozon", "olx"],
        source=kaspi,
        olx_source=olx,
        persist=False,
    )

    assert result.recommendation.best.candidate.source == "kaspi"
    assert result.unavailable_sources == ["ozon"]
    assert olx.queries


async def test_one_source_failure_does_not_kill_run() -> None:
    kaspi = FakeSource(
        "kaspi", [_rp("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", 30000)]
    )
    ozon = FakeSource("ozon", [], fail=True)  # simulate Ozon browser dying
    olx = FakeSource("olx", [])

    result = await recommend(
        "ирригатор портативный, до 40000 тг",
        use_llm=False,
        sources=["kaspi", "ozon", "olx"],
        source=kaspi,
        ozon_source=ozon,
        olx_source=olx,
        persist=False,
    )

    # Ozon failed, but Kaspi still produced a recommendation.
    assert result.recommendation.best.candidate.source == "kaspi"


async def test_one_bad_candidate_specs_does_not_skip_whole_kaspi_source() -> None:
    """A single 404 (e.g. a fashion-card id with no content page) must not
    take down the entire Kaspi source — only that candidate loses its specs.
    """
    kaspi = FakeSource(
        "kaspi",
        [
            _rp("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", 30000),
            _rp("kaspi", "k2", "Revyline RL-100 ирригатор портативный", 25000),
        ],
        specs_fail_ids={"k1"},
    )

    result = await recommend(
        "ирригатор портативный, до 40000 тг",
        use_llm=False,
        sources=["kaspi"],
        source=kaspi,
        persist=False,
    )

    pool_ids = {result.recommendation.best.candidate.source_product_id} | {
        s.candidate.source_product_id for s in result.recommendation.alternatives.values()
    }
    # Both candidates still made it into the ranking pool ("k1" with empty specs).
    assert pool_ids & {"k1", "k2"}
    assert result.recommendation.best.candidate.source == "kaspi"


async def test_fashion_variant_candidates_skip_specs_fetch_entirely(caplog) -> None:
    """Cards with ``specs_available=False`` (fashion variant ids) never hit specs().

    Live evidence: 12 consecutive Kaspi specs 404s (~2s each) for a "кроссовки"
    query, because every candidate was a variant-letter id the content API
    simply has no page for. That should now cost zero HTTP calls, and the
    per-source enrichment loop should emit exactly one aggregated WARNING.
    """
    import logging

    kaspi = FakeSource(
        "kaspi",
        [
            RawProduct(
                source="kaspi",
                product_id="16540138",
                title="Кроссовки серый",
                price=15000,
                specs_available=False,
            ),
            RawProduct(
                source="kaspi",
                product_id="16540139",
                title="Кроссовки синий",
                price=16000,
                specs_available=False,
            ),
        ],
    )

    with caplog.at_level(logging.WARNING, logger="buysmarter.services.collect"):
        result = await recommend(
            "кроссовки летние",
            use_llm=False,
            sources=["kaspi"],
            source=kaspi,
            persist=False,
        )

    assert kaspi.specs_requests == []  # specs() never called for either candidate
    assert result.recommendation.best.candidate.source == "kaspi"
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings.count("kaspi: specs unavailable for 2 of 2 candidates") == 1


async def test_specs_fetch_failure_logged_at_info_not_warning(caplog) -> None:
    """A real per-item specs 404 (specs_available=True but the call raises)
    is logged at INFO (file-only), not console WARNING — only the aggregated
    summary line should reach WARNING level.
    """
    import logging

    kaspi = FakeSource(
        "kaspi",
        [_rp("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", 30000)],
        specs_fail_ids={"k1"},
    )

    with caplog.at_level(logging.INFO, logger="buysmarter.services.collect"):
        await recommend(
            "ирригатор портативный, до 40000 тг",
            use_llm=False,
            sources=["kaspi"],
            source=kaspi,
            persist=False,
        )

    per_item = [r for r in caplog.records if "Kaspi specs fetch failed for product k1" in r.message]
    assert len(per_item) == 1
    assert per_item[0].levelno == logging.INFO
    aggregate = [
        r for r in caplog.records if r.message == "kaspi: specs unavailable for 1 of 1 candidates"
    ]
    assert len(aggregate) == 1
    assert aggregate[0].levelno == logging.WARNING


async def test_used_olx_section_matches_only_relevant_cheaper_listings() -> None:
    kaspi = FakeSource(
        "kaspi", [_rp("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", 30000)]
    )
    olx = FakeSource(
        "olx",
        [
            _rp("olx", "u1", "Waterpik WP-100 ирригатор портативный", 15000),
            _rp("olx", "u2", "Диван кожаный угловой", 12000),
        ],
        specs_map={
            "u1": {"condition": "Б/у", "city": "Алматы"},
            "u2": {"condition": "Б/у", "city": "Алматы"},
        },
    )

    result = await recommend(
        "ирригатор портативный, до 40000 тг",
        use_llm=False,
        sources=["kaspi", "olx"],
        source=kaspi,
        olx_source=olx,
        persist=False,
    )

    used = result.used_listings
    assert len(used) == 1
    assert used[0].title.startswith("Waterpik")
    assert used[0].price == 15000
    assert used[0].saving == 15000  # 30000 new - 15000 used
    assert used[0].city == "Алматы"
    assert used[0].con == "без гарантии/проверки"


async def test_used_listing_ignored_when_not_meaningfully_cheaper() -> None:
    kaspi = FakeSource(
        "kaspi", [_rp("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", 30000)]
    )
    olx = FakeSource(
        "olx",
        [_rp("olx", "u1", "Waterpik WP-100 ирригатор портативный", 25000)],  # only ~17% off
        specs_map={"u1": {"condition": "Б/у"}},
    )

    result = await recommend(
        "ирригатор портативный, до 40000 тг",
        use_llm=False,
        sources=["kaspi", "olx"],
        source=kaspi,
        olx_source=olx,
        persist=False,
    )

    # No new-product-matched listing (not meaningfully cheaper) — but the
    # item is still a tier-A Waterpik within budget, so it surfaces as a
    # standalone "notable used" deal (Fix 3c, independent of any match).
    assert all(listing.standalone for listing in result.used_listings)


# --- Review-trust second pass ---------------------------------------------


class ReviewingKaspiSource:
    """Kaspi double serving a product with rating/reviews + fake-looking reviews."""

    name = "kaspi"

    def __init__(self, product: RawProduct, reviews) -> None:
        self._product = product
        self._reviews = reviews
        self.review_requests: list[str] = []

    async def search(self, query: str, city: str | None = None) -> list[RawProduct]:
        return [self._product]

    async def specs(self, product_id: str) -> dict[str, str]:
        return {}

    async def offers(self, product_id: str) -> list[RawOffer]:
        return []

    async def reviews(self, product_id: str):
        self.review_requests.append(product_id)
        return self._reviews


async def test_review_trust_pass_discounts_fake_reviews_of_top_kaspi() -> None:
    from market_sources.base import RawReview

    product = RawProduct(
        source="kaspi",
        product_id="k1",
        title="Waterpik WP-100 ирригатор портативный",
        price=30000,
        rating=5.0,
        review_count=200,
    )
    # 25 all-five-star, empty-text reviews → strong fake signal.
    fake_reviews = [
        RawReview(source="kaspi", product_id="k1", rating=5, text="", created_at="01.05.2023")
        for _ in range(25)
    ]
    kaspi = ReviewingKaspiSource(product, fake_reviews)

    result = await recommend(
        "ирригатор портативный, до 40000 тг",
        use_llm=False,
        sources=["kaspi"],
        source=kaspi,
        persist=False,
    )

    assert kaspi.review_requests == ["k1"]
    reviews_axis = next(a for a in result.recommendation.best.axes if a.name == "reviews")
    assert "review trust" in reviews_axis.explanation


# --- Pure helpers ---------------------------------------------------------


def _cand(source: str, pid: str, title: str, brand: str | None, price: int) -> Candidate:
    return Candidate(source=source, source_product_id=pid, title=title, brand=brand, price=price)


def test_dedupe_cross_source_keeps_cheaper_and_notes_other() -> None:
    kaspi = _cand("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", "Waterpik", 30000)
    ozon = _cand("ozon", "o1", "Waterpik WP-100 ирригатор портативный", "Waterpik", 25000)

    kept, notes = dedupe_cross_source([kaspi, ozon])

    assert len(kept) == 1
    assert kept[0].source == "ozon"  # cheaper survives
    assert kept[0].price == 25000
    note = notes[("ozon", "o1")]
    assert "kaspi" in note.text and "30" in note.text


def test_dedupe_cross_source_sets_dup_price_gap_on_big_gap() -> None:
    cheap = _cand("kaspi", "k1", "Vivo TWS Air 3 наушники", "Vivo", 3_208)
    pricier = _cand("ozon", "o1", "Vivo TWS Air 3 наушники", "Vivo", 11_186)

    kept, notes = dedupe_cross_source([cheap, pricier])

    assert len(kept) == 1
    assert kept[0].price == 3_208
    assert kept[0].dup_price_gap == pytest.approx(11_186 / 3_208)
    assert notes[("kaspi", "k1")].text.startswith("также на ozon")
    assert notes[("kaspi", "k1")].gap == pytest.approx(11_186 / 3_208)


def test_dedupe_cross_source_leaves_dup_price_gap_none_for_mild_gap() -> None:
    cheap = _cand("kaspi", "k1", "Waterpik WP-100 ирригатор", "Waterpik", 10_000)
    pricier = _cand("ozon", "o1", "Waterpik WP-100 ирригатор", "Waterpik", 12_000)

    kept, _notes = dedupe_cross_source([cheap, pricier])

    assert len(kept) == 1
    assert kept[0].dup_price_gap is None


def test_dedupe_cross_source_does_not_mutate_input_candidates() -> None:
    """M2: dedupe_cross_source is pure — the surviving candidate is a fresh
    copy when a dup_price_gap needs recording; the originals are untouched."""
    cheap = _cand("kaspi", "k1", "Vivo TWS Air 3 наушники", "Vivo", 3_208)
    pricier = _cand("ozon", "o1", "Vivo TWS Air 3 наушники", "Vivo", 11_186)

    kept, _notes = dedupe_cross_source([cheap, pricier])

    assert cheap.dup_price_gap is None  # input untouched
    assert pricier.dup_price_gap is None  # input untouched
    assert kept[0].dup_price_gap == pytest.approx(11_186 / 3_208)  # only the copy carries it
    assert kept[0] is not cheap


def test_dedupe_cross_source_three_way_duplicate_keeps_max_gap() -> None:
    """M3: with 3+ duplicates of the same product, dup_price_gap is the MAX
    gap ever observed across the group, not just the last comparison — here
    the last-processed duplicate (7,000) has a SMALLER gap than an earlier
    one (20,000), and the max (from 20,000) must still win."""
    cheapest = _cand("kaspi", "k1", "Vivo TWS Air 3 наушники", "Vivo", 3_000)
    biggest_gap = _cand("ozon", "o1", "Vivo TWS Air 3 наушники", "Vivo", 20_000)
    smaller_gap = _cand("olx", "u1", "Vivo TWS Air 3 наушники", "Vivo", 7_000)

    kept, _notes = dedupe_cross_source([cheapest, biggest_gap, smaller_gap])

    assert len(kept) == 1
    assert kept[0].price == 3_000
    assert kept[0].dup_price_gap == pytest.approx(20_000 / 3_000)


def test_dedupe_cross_source_dup_price_gap_order_independent_all_permutations():
    """HIGH-2: verified bug — the survivor's dup_price_gap (and note) used to
    depend on arrival order because the gap was read off the CURRENT pairwise
    "pricier" loser, losing the group's true spread when the priciest member
    got evicted from that role by a later, even-cheaper arrival. All 6
    orderings of the same 100k/90k/50k trio must yield an identical gap AND
    an identical note (source + price of the group's true priciest member)."""
    import itertools

    kaspi_100k = _cand("kaspi", "k1", "Vivo TWS Air 3 наушники", "Vivo", 100_000)
    ozon_90k = _cand("ozon", "o1", "Vivo TWS Air 3 наушники", "Vivo", 90_000)
    olx_50k = _cand("olx", "u1", "Vivo TWS Air 3 наушники", "Vivo", 50_000)
    trio = {"kaspi": kaspi_100k, "ozon": ozon_90k, "olx": olx_50k}

    results = []
    for order in itertools.permutations(trio.values()):
        kept, notes = dedupe_cross_source(list(order))
        assert len(kept) == 1
        survivor = kept[0]
        assert survivor.source == "olx" and survivor.price == 50_000  # true group min
        note = notes[(survivor.source, survivor.source_product_id)]
        results.append((survivor.dup_price_gap, note.text, note.gap))

    # Every permutation agrees: gap == 100000/50000 == 2.0, note names the
    # group's true priciest member (kaspi @ 100,000), never a lesser one.
    assert len({r for r in results}) == 1
    gap, text, display_gap = results[0]
    assert gap == pytest.approx(2.0)
    assert display_gap == pytest.approx(2.0)
    assert "kaspi" in text and "100\xa0000" in text


def test_dedupe_cross_source_zero_price_duplicate_never_wins_as_cheaper() -> None:
    """M5: a zero-price (missing-price) duplicate must not be treated as the
    "cheaper" survivor just because 0 < any positive price."""
    priced = _cand("kaspi", "k1", "Waterpik WP-100 ирригатор", "Waterpik", 15_000)
    zero_priced = _cand("ozon", "o1", "Waterpik WP-100 ирригатор", "Waterpik", 0)

    kept, notes = dedupe_cross_source([priced, zero_priced])

    assert len(kept) == 1
    assert kept[0].source == "kaspi"
    assert kept[0].price == 15_000
    assert notes[("kaspi", "k1")].text.startswith("также на ozon")

    # Order reversed: the positive-priced listing must still win.
    kept_rev, notes_rev = dedupe_cross_source([zero_priced, priced])
    assert kept_rev[0].source == "kaspi"
    assert kept_rev[0].price == 15_000
    assert notes_rev[("kaspi", "k1")].text.startswith("также на ozon")


def test_dedupe_keeps_distinct_products() -> None:
    a = _cand("kaspi", "k1", "Waterpik WP-100 ирригатор", "Waterpik", 30000)
    b = _cand("ozon", "o1", "Revyline RL-650 ирригатор", "Revyline", 18000)

    kept, notes = dedupe_cross_source([a, b])

    assert len(kept) == 2
    assert notes == {}


def test_match_used_listings_requires_brand_and_price_gap() -> None:
    from buysmarter.knowledge.brands import IRRIGATOR_BRAND_BOOK

    new_ref = _cand("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", "Waterpik", 30000)
    matching = _cand("olx", "u1", "Waterpik WP-100 ирригатор", "Waterpik", 15000)
    matching.specs["city"] = "Алматы"
    unrelated = _cand("olx", "u2", "Диван кожаный угловой", None, 5000)

    listings = match_used_listings([new_ref], [matching, unrelated], IRRIGATOR_BRAND_BOOK)

    assert len(listings) == 1
    assert listings[0].title.startswith("Waterpik")
    assert listings[0].saving == 15000


def test_match_used_listings_via_canonical_model() -> None:
    from buysmarter.knowledge.models_book import IRRIGATOR_MODEL_BOOK

    # New product carries the MEO705 code; the OLX ad uses the "me 705" typo —
    # only ModelBook canonical equality links them (titles barely overlap).
    new_ref = _cand("kaspi", "k1", "Xiaomi Water Flosser 2 MEO705", "Xiaomi", 30000)
    used = _cand("olx", "u1", "Ирригатор xiaomi me 705", "Xiaomi", 12000)
    used.specs["city"] = "Алматы"

    listings = match_used_listings([new_ref], [used], None, IRRIGATOR_MODEL_BOOK)

    assert len(listings) == 1
    assert listings[0].saving == 18000
    assert listings[0].canonical == "Xiaomi Water Flosser 2 (MEO705)"


def test_dedupe_cross_source_merges_via_canonical_model() -> None:
    from buysmarter.knowledge.models_book import IRRIGATOR_MODEL_BOOK

    # Same product, two codes (F300 == MEO703), two sources, low title overlap:
    # canonical equality merges them where token overlap alone would not.
    kaspi = _cand("kaspi", "k1", "Xiaomi Mijia F300 ирригатор", "Xiaomi", 9000)
    ozon = _cand("ozon", "o1", "Ирригатор Сяоми MEO703", "Xiaomi", 7000)

    kept, notes = dedupe_cross_source([kaspi, ozon], IRRIGATOR_MODEL_BOOK)

    assert len(kept) == 1
    assert kept[0].price == 7000  # cheaper survives
    assert notes[("ozon", "o1")].text.startswith("также на kaspi")


# --- Review-summary penalty pass ------------------------------------------


from buysmarter.llm.review_summary import ReviewSummary  # noqa: E402
from buysmarter.ranking.models import ScoredCandidate  # noqa: E402
from buysmarter.ranking.selection import assemble_recommendation  # noqa: E402
from buysmarter.services import review_pass as review_mod  # noqa: E402


def _scored(pid: str, total: float) -> ScoredCandidate:
    return ScoredCandidate(
        candidate=Candidate(
            source="kaspi",
            source_product_id=pid,
            title=f"product {pid}",
            brand="Waterpik",
            brand_tier="A",
            price=20_000,
            rating=4.8,
            review_count=100,
        ),
        total_score=total,
        confidence=1.0,
        axes=[],
    )


def test_summary_penalty_re_sort_changes_best_pick_when_top_counterfeit():
    top = _scored("k1", 90.0)  # ranks #1 pre-penalty, counterfeit-flagged
    clean = _scored("k2", 80.0)  # #2, clean
    recommendation = assemble_recommendation([top, clean], 0)
    assert recommendation.best.candidate.source_product_id == "k1"

    summaries = {("kaspi", "k1"): ReviewSummary(counterfeit_suspicion=True)}
    out = review_mod._apply_summary_penalties(recommendation, summaries)

    # 90 * 0.6 = 54 < 80 → the clean #2 becomes the best pick.
    assert out.best.candidate.source_product_id == "k2"
    penalized = next(s for s in out.ranked if s.candidate.source_product_id == "k1")
    assert penalized.total_score == 54.0
    assert penalized.confidence == 0.6  # confidence reduced too
    assert penalized.review_penalty == 36.0
    assert "отзывы подозревают подделку" in penalized.cons


def test_summary_penalty_pass_noop_without_summaries():
    recommendation = assemble_recommendation([_scored("k1", 90.0), _scored("k2", 80.0)], 0)
    out = review_mod._apply_summary_penalties(recommendation, {})
    assert out is recommendation


class _MultiReviewKaspi:
    """Kaspi double serving per-product reviews (list length is controllable)."""

    name = "kaspi"

    def __init__(self, reviews_by_pid: dict[str, list]) -> None:
        self._reviews = reviews_by_pid
        self.requested: list[str] = []

    async def reviews(self, product_id: str):
        self.requested.append(product_id)
        return self._reviews.get(product_id, [])


def _reviews(pid: str, n: int) -> list:
    from market_sources.base import RawReview

    return [
        RawReview(source="kaspi", product_id=pid, text=f"подробный честный отзыв номер {i}")
        for i in range(n)
    ]


async def test_summarize_top_skips_candidates_with_too_few_reviews(monkeypatch):
    calls: list[str] = []

    async def _fake_summarize(reviews, features=None):
        calls.append(reviews[0].product_id)
        return ReviewSummary()

    monkeypatch.setattr(review_mod, "summarize_reviews", _fake_summarize)

    kaspi = _MultiReviewKaspi({"k1": _reviews("k1", 6), "k2": _reviews("k2", 3)})
    recommendation = assemble_recommendation([_scored("k1", 90.0), _scored("k2", 80.0)], 0)
    reviews_by_id = {("kaspi", "k1"): _reviews("k1", 6), ("kaspi", "k2"): _reviews("k2", 3)}

    summaries = await review_mod._summarize_top(recommendation, {"kaspi": kaspi}, reviews_by_id)

    assert isinstance(summaries[("kaspi", "k1")], ReviewSummary)  # 6 reviews → summarized
    assert isinstance(summaries[("kaspi", "k2")], str)  # 3 reviews (< 5) → skipped, with why
    assert calls == ["k1"]


async def test_review_pass_penalizes_counterfeit_and_flips_best(monkeypatch):
    def _summary_for(pid: str) -> ReviewSummary:
        if pid == "k1":
            return ReviewSummary(
                counterfeit_suspicion=True,
                reliability_concern="serious",
                red_flags=["кожа сгорает"],
            )
        return ReviewSummary(pros=["работает"])

    async def _fake_summarize(reviews, features=None):
        return _summary_for(reviews[0].product_id)

    monkeypatch.setattr(review_mod, "summarize_reviews", _fake_summarize)

    kaspi = _MultiReviewKaspi({"k1": _reviews("k1", 8), "k2": _reviews("k2", 8)})
    recommendation = assemble_recommendation([_scored("k1", 90.0), _scored("k2", 80.0)], 0)

    out, summary, _note = await review_mod.review_pass(
        recommendation, {"kaspi": kaspi}, use_llm=True, with_review_summary=True
    )

    assert out.best.candidate.source_product_id == "k2"  # clean pick won
    assert summary is not None and summary.pros == ["работает"]  # panel = new best's summary
    k1 = next(s for s in out.ranked if s.candidate.source_product_id == "k1")
    assert k1.review_penalty > 0
    assert "кожа сгорает" in k1.cons


# --- #14: the best pick after the summary penalties gets its own summary --------


def _counterfeit_pass(fakes: set[str], calls: list[str]):
    async def _fake_summarize(reviews, features=None):
        pid = reviews[0].product_id
        calls.append(pid)
        if pid in fakes:
            return ReviewSummary(counterfeit_suspicion=True)
        return ReviewSummary(pros=[f"хорош {pid}"])

    return _fake_summarize


async def _pass_over(
    monkeypatch, scores: list[float], fakes: set[str], few: frozenset[str] = frozenset()
):
    calls: list[str] = []
    monkeypatch.setattr(review_mod, "summarize_reviews", _counterfeit_pass(fakes, calls))
    pids = [f"k{i}" for i in range(1, len(scores) + 1)]
    kaspi = _MultiReviewKaspi({p: _reviews(p, 2 if p in few else 8) for p in pids})
    recommendation = assemble_recommendation(
        [_scored(p, total) for p, total in zip(pids, scores, strict=True)], 0
    )
    out, summary, note = await review_mod.review_pass(
        recommendation, {"kaspi": kaspi}, use_llm=True, with_review_summary=True
    )
    return out, summary, note, calls


def _total(out, pid: str) -> float:
    return next(s.total_score for s in out.ranked if s.candidate.source_product_id == pid)


async def test_a_new_best_below_the_summarized_top_gets_its_own_summary(monkeypatch) -> None:
    out, summary, note, calls = await _pass_over(
        monkeypatch, [90, 85, 80, 75, 70], {"k1", "k2", "k3"}
    )

    assert out.best.candidate.source_product_id == "k4"
    assert summary is not None and summary.pros == ["хорош k4"]
    assert note is None
    assert sorted(calls) == ["k1", "k2", "k3", "k4"]
    assert _total(out, "k1") == 54.0  # each summary penalizes once


async def test_a_counterfeit_new_best_hands_over_to_the_next(monkeypatch) -> None:
    out, summary, _note, calls = await _pass_over(
        monkeypatch, [90, 85, 80, 75, 70], {"k1", "k2", "k3", "k4"}
    )

    assert out.best.candidate.source_product_id == "k5"
    assert summary is not None and summary.pros == ["хорош k5"]
    assert calls[3:] == ["k4", "k5"]
    assert _total(out, "k4") == 45.0


async def test_extra_summaries_stop_after_three(monkeypatch) -> None:
    fakes = {"k1", "k2", "k3", "k4", "k5", "k6"}
    out, summary, note, calls = await _pass_over(monkeypatch, [90, 85, 80, 75, 70, 65, 60], fakes)

    assert out.best.candidate.source_product_id == "k7"
    assert summary is None and note == review_mod._NOT_SUMMARIZED
    assert len(calls) == 6


async def test_a_new_best_with_few_reviews_says_why(monkeypatch) -> None:
    out, summary, note, calls = await _pass_over(
        monkeypatch, [90, 85, 80, 75], {"k1", "k2", "k3"}, few=frozenset({"k4"})
    )

    assert out.best.candidate.source_product_id == "k4"
    assert summary is None and note is not None and "мало текстов отзывов" in note
    assert len(calls) == 3


# --- Fix 1a: latin-jargon stripping for search queries ----------------------


def test_strip_latin_tokens_drops_jargon_acronym():
    assert strip_latin_tokens("TWS наушники") == "наушники"


def test_strip_latin_tokens_no_latin_tokens_returns_none():
    assert strip_latin_tokens("робот-пылесос") is None


def test_strip_latin_tokens_digit_jargon():
    assert strip_latin_tokens("смартфон 5G") == "смартфон"


def test_strip_latin_tokens_all_latin_category_returns_none():
    # Stripping would empty the string out entirely — nothing left to search.
    assert strip_latin_tokens("iphone case") is None


# --- M1: a book-known brand token is kept, not stripped as jargon ----------


def test_strip_latin_tokens_keeps_known_brand_with_book():
    book = BrandBook(tiers={"Apple": "A"}, aliases={})
    assert strip_latin_tokens("наушники Apple", book) is None  # nothing to strip


def test_strip_latin_tokens_still_strips_unknown_jargon_with_book():
    book = BrandBook(tiers={"Apple": "A"}, aliases={})
    assert strip_latin_tokens("TWS наушники", book) == "наушники"  # TWS unknown to book


def test_strip_latin_tokens_without_book_strips_every_latin_token():
    # No book -> deterministic fallback: even a real brand name gets stripped.
    assert strip_latin_tokens("наушники Apple") == "наушники"


# --- Fix 1b: sub-brand alias searches + _MAX_BRAND_SEARCHES cap -------------


def test_brand_search_plan_includes_redmi_style_sub_brand_alias():
    book = BrandBook(tiers={"Xiaomi": "C"}, aliases={"Redmi": "Xiaomi"})
    plan = _brand_search_plan(book)
    assert ("Xiaomi", "Xiaomi") in plan
    assert ("Redmi", "Xiaomi") in plan


def test_brand_search_plan_excludes_formatting_respelling_alias():
    book = BrandBook(
        tiers={"Oral-B": "A", "B.Well": "B"},
        aliases={"OralB": "Oral-B", "B Well": "B.Well"},
    )
    plan = _brand_search_plan(book)
    terms = {term for term, _canonical in plan}
    assert "OralB" not in terms
    assert "B Well" not in terms


def test_brand_search_plan_excludes_cyrillic_alias():
    book = BrandBook(tiers={"Xiaomi": "C"}, aliases={"Сяоми": "Xiaomi"})
    plan = _brand_search_plan(book)
    terms = {term for term, _canonical in plan}
    assert "Сяоми" not in terms


def test_brand_search_plan_excludes_alias_of_untargeted_canonical():
    # "SomeBrand" is tier D (not A/B, and not the special-cased "Xiaomi"
    # best-value carve-out) — not itself a target — so its alias must not be
    # searched either.
    book = BrandBook(tiers={"SomeBrand": "D"}, aliases={"AliasOfIt": "SomeBrand"})
    plan = _brand_search_plan(book)
    assert plan == []


def test_brand_search_plan_respects_max_brand_searches_cap():
    tiers = {f"Brand{i}": "A" for i in range(40)}
    book = BrandBook(tiers=tiers, aliases={})
    plan = _brand_search_plan(book)
    assert len(plan) == 30  # _MAX_BRAND_SEARCHES


# --- HIGH-1: alias-shadowed canonical (dedupe + resolve-derived canonical) --


def test_brand_search_plan_dedupes_and_resolves_alias_shadowed_canonical():
    """ "Honor" is simultaneously a tier-A canonical AND a known alias of
    "Huawei" — a real book shape (Honor spun off from Huawei but the alias
    map still treats old "Honor Choice" sub-line cards as Huawei's). Two
    bugs this must not reproduce: (a) "Honor" searched twice (once as its own
    canonical, once again while scanning aliases-of-Huawei); (b) the plan
    entry storing "Honor" as its own canonical, when ``resolve("Honor"))``
    (aliases win priority) actually returns "Huawei" — which would make
    EVERY card's per-card check ``resolve(title)[0] == canonical`` fail."""
    book = BrandBook(tiers={"Honor": "A", "Huawei": "A"}, aliases={"Honor": "Huawei"})

    plan = _brand_search_plan(book)

    terms = [term for term, _canonical in plan]
    assert terms.count("Honor") == 1  # not searched twice
    honor_entry = next(entry for entry in plan if entry[0] == "Honor")
    assert honor_entry == ("Honor", "Huawei")  # matches what resolve("Honor") returns


async def test_brand_targeted_products_survives_alias_shadowed_canonical():
    """End-to-end: with the old (buggy) tiers-dict-key canonical, every card
    returned by the "Honor <category>" search would be silently dropped
    (resolve(title)[0] == "Huawei" != stored canonical "Honor"). Must now
    survive."""
    book = BrandBook(tiers={"Honor": "A", "Huawei": "A"}, aliases={"Honor": "Huawei"})

    class _HonorSource:
        name = "kaspi"

        async def search(self, query: str, city: str | None = None):
            if query.startswith("Honor"):
                return [
                    RawProduct(
                        source="kaspi",
                        product_id="h1",
                        title="Honor Choice Earbuds X5 наушники",
                        price=8000,
                    )
                ]
            return []

    found = await _brand_targeted_products(_HonorSource(), "наушники", None, book)

    assert any(p.product_id == "h1" for p in found)


# --- HIGH-4: tier-A-first plan ordering (dict-iteration-order independent) --


def test_brand_search_plan_orders_tier_a_before_tier_b_regardless_of_dict_order():
    # Insertion order deliberately puts tier-B brands first in the dict —
    # the plan must still list tier-A ones first, so a cap trims the LEAST
    # important brand, not an arbitrary dict-order casualty.
    book = BrandBook(
        tiers={"BBrand1": "B", "BBrand2": "B", "ABrand1": "A", "ABrand2": "A"},
        aliases={},
    )

    plan = _brand_search_plan(book)

    terms = [term for term, _canonical in plan]
    a_positions = [terms.index("ABrand1"), terms.index("ABrand2")]
    b_positions = [terms.index("BBrand1"), terms.index("BBrand2")]
    assert max(a_positions) < min(b_positions)


def test_brand_search_plan_alias_ordered_right_after_its_own_canonical():
    book = BrandBook(
        tiers={"Xiaomi": "C", "Waterpik": "A"},  # Xiaomi = best-value-C, listed last
        aliases={"Redmi": "Xiaomi"},
    )

    plan = _brand_search_plan(book)

    terms = [term for term, _canonical in plan]
    assert terms.index("Waterpik") < terms.index("Xiaomi") < terms.index("Redmi")


# --- Fix 1c: OLX head-noun regression ----------------------------------------


def test_head_noun_tws_naushniki_regression():
    """ "TWS" (len 3) is dropped by the min-length filter, leaving a single
    surviving token — it must still be searched since it narrows the
    two-word original, not silently return None."""
    assert _head_noun("TWS наушники") == "наушники"


def test_head_noun_single_word_category_returns_none():
    assert _head_noun("ирригатор") is None


def test_head_noun_multi_word_returns_last_token():
    assert _head_noun("робот-пылесос") == "пылесос"


# --- HIGH-3a: _head_noun must not degenerate into a bare brand token --------


def test_head_noun_skips_brand_token_falls_back_to_previous():
    book = BrandBook(tiers={"Sony": "A"}, aliases={})
    assert _head_noun("беспроводные наушники Sony", book) == "наушники"


def test_head_noun_without_book_keeps_old_behavior():
    # No book -> deterministic fallback, same as before (brand-unaware).
    assert _head_noun("беспроводные наушники Sony") == "sony"


# --- Fix 1b: targeted hits exempt from _MAX_CARDS ---------------------------


class _CountingKaspiSource:
    """Fake ProductSource: N noisy main-search hits + one targeted brand hit,
    to prove the targeted hit survives `_MAX_CARDS` truncation of the noisy
    pool (Fix 1b)."""

    name = "kaspi"

    def __init__(self, noisy_count: int) -> None:
        self._noisy = [
            RawProduct(
                source="kaspi", product_id=f"noisy{i}", title=f"Noname товар {i}", price=1000
            )
            for i in range(noisy_count)
        ]
        self._brand_hit = RawProduct(
            source="kaspi", product_id="brand1", title="Waterpik WP-100 ирригатор", price=35000
        )
        self.specs_requests: list[str] = []

    async def search(self, query: str, city: str | None = None):
        if query.startswith("Waterpik"):
            return [self._brand_hit]
        return self._noisy

    async def specs(self, product_id: str):
        self.specs_requests.append(product_id)
        return {}

    async def offers(self, product_id: str):
        return []


async def test_collect_kaspi_targeted_hit_exempt_from_max_cards_truncation():
    book = BrandBook(tiers={"Waterpik": "A"}, aliases={})
    source = _CountingKaspiSource(noisy_count=30)  # far more than _MAX_CARDS (16)

    candidates, _rows = await _collect_kaspi(source, "ирригатор", None, book)

    ids = {c.source_product_id for c in candidates}
    assert "brand1" in ids  # targeted hit survives despite 30 noisy competitors


# --- HIGH-5: offer-fetch fairness across targeted vs. main pool -------------


class _OfferCountingKaspiSource:
    """Fake ProductSource: several brand-targeted searches (each capped at
    ``_BRAND_SEARCH_TOP_N`` matched cards, same as production) + many
    main-pool hits, records which product ids had ``offers`` actually
    fetched, to prove the fetch budget is split evenly instead of always
    favoring targeted hits (HIGH-5)."""

    name = "kaspi"

    def __init__(self, brands: list[str], main_count: int) -> None:
        self._brands = brands
        self._main = [
            RawProduct(
                source="kaspi", product_id=f"noisy{i}", title=f"Noname товар {i}", price=1000
            )
            for i in range(main_count)
        ]
        self.offer_requests: list[str] = []

    async def search(self, query: str, city: str | None = None):
        for brand in self._brands:
            if query.startswith(brand):
                # 2 matched cards per brand search (mirrors _BRAND_SEARCH_TOP_N).
                return [
                    RawProduct(
                        source="kaspi",
                        product_id=f"brand-{brand}-{i}",
                        title=f"{brand} WP-{i} ирригатор",
                        price=30000,
                    )
                    for i in range(2)
                ]
        return self._main

    async def specs(self, product_id: str):
        return {}

    async def offers(self, product_id: str):
        self.offer_requests.append(product_id)
        return []


async def test_collect_kaspi_splits_offer_fetch_between_targeted_and_main_pool():
    brands = ["Waterpik", "Panasonic", "Burst"]  # 3 brands x 2 cards = 6 targeted hits
    book = BrandBook(tiers={b: "A" for b in brands}, aliases={})
    source = _OfferCountingKaspiSource(brands=brands, main_count=16)

    await _collect_kaspi(source, "ирригатор", None, book)

    targeted_offers = [r for r in source.offer_requests if r.startswith("brand-")]
    main_offers = [r for r in source.offer_requests if r.startswith("noisy")]
    assert len(targeted_offers) == 4  # _OFFERS_FOR // 2 (out of 6 targeted candidates)
    assert len(main_offers) == 4  # _OFFERS_FOR // 2
    assert len(source.offer_requests) == 8  # _OFFERS_FOR total, unchanged


async def test_collect_kaspi_gives_the_unused_offer_share_to_the_other_pool():
    # One targeted hit (2 cards): its unused 2 offer fetches go to the main pool (#22).
    book = BrandBook(tiers={"Waterpik": "A"}, aliases={})
    source = _OfferCountingKaspiSource(brands=["Waterpik"], main_count=16)

    await _collect_kaspi(source, "ирригатор", None, book)

    assert len([r for r in source.offer_requests if r.startswith("brand-")]) == 2
    assert len([r for r in source.offer_requests if r.startswith("noisy")]) == 6


# --- M2: jargon-only category skips the title-word check -------------------


async def test_brand_targeted_products_jargon_only_category_skips_title_check():
    """A category that is ENTIRELY latin/jargon (e.g. bare "TWS") has no real
    category word to check the title against — a brand-confirmed card must
    not be rejected just because real listings never carry the jargon."""
    book = BrandBook(tiers={"JBL": "A"}, aliases={})

    class _JargonOnlySource:
        name = "kaspi"

        async def search(self, query: str, city: str | None = None):
            if query.startswith("JBL"):
                return [
                    RawProduct(source="kaspi", product_id="j1", title="JBL Tune 510BT", price=15000)
                ]
            return []

    found = await _brand_targeted_products(_JargonOnlySource(), "TWS", None, book)

    assert any(p.product_id == "j1" for p in found)  # not rejected for lacking "tws" in title


# --- HIGH-3b: degenerate recall query -> downstream category-match safety net


async def test_collect_olx_rejects_off_category_junk_from_degenerate_recall():
    """A head-noun/stripped-variant search broadened too far can return ads
    with no real relation to the category — the downstream title-match check
    must reject them even though they were technically "found"."""
    book = BrandBook(tiers={"Sony": "A"}, aliases={})

    class _JunkOlxSource:
        name = "olx"

        async def search(self, query: str, city: str | None = None):
            return [
                RawProduct(
                    source="olx",
                    product_id="j1",
                    title="Продам гараж, недорого",  # utterly unrelated
                    price=500000,
                )
            ]

        async def specs(self, product_id: str):
            return {"condition": "б/у"}

    used = await _collect_olx(_JunkOlxSource(), "наушники Sony", book, None)

    assert used == []


# --- Fix 3c: notable_used_listings -------------------------------------------


def _used_cand(source: str, pid: str, title: str, price: int) -> Candidate:
    return Candidate(source=source, source_product_id=pid, title=title, price=price)


def test_notable_used_listings_tier_filter_and_sort():
    book = BrandBook(tiers={"Waterpik": "A", "Revyline": "B"}, aliases={})
    tier_a = _used_cand("olx", "u1", "Waterpik WP-660 ирригатор б/у", 20000)
    tier_b_cheap = _used_cand("olx", "u2", "Revyline RL-650 ирригатор", 5000)
    untiered = _used_cand("olx", "u3", "Noname ирригатор", 3000)

    listings = notable_used_listings(
        [tier_a, tier_b_cheap, untiered], book, budget=100000, exclude=set(), category="ирригатор"
    )

    titles = [listing.title for listing in listings]
    assert untiered.title not in titles  # untiered brand excluded
    assert titles == [tier_a.title, tier_b_cheap.title]  # tier A before tier B
    assert all(listing.standalone for listing in listings)
    assert all(listing.saving == 0 and listing.new_price == 0 for listing in listings)


def test_notable_used_listings_respects_budget():
    book = BrandBook(tiers={"Waterpik": "A"}, aliases={})
    over_budget = _used_cand("olx", "u1", "Waterpik WP-660 ирригатор", 50000)
    within_budget = _used_cand("olx", "u2", "Waterpik WP-100 ирригатор", 15000)

    listings = notable_used_listings(
        [over_budget, within_budget], book, budget=40000, exclude=set(), category="ирригатор"
    )

    assert [listing.title for listing in listings] == [within_budget.title]


def test_notable_used_listings_excludes_already_matched():
    book = BrandBook(tiers={"Waterpik": "A"}, aliases={})
    already_matched = _used_cand("olx", "u1", "Waterpik WP-660 ирригатор", 20000)

    listings = notable_used_listings(
        [already_matched],
        book,
        budget=100000,
        exclude={already_matched.title},
        category="ирригатор",
    )

    assert listings == []


def test_notable_used_listings_capped_at_top_n():
    book = BrandBook(tiers={"Waterpik": "A"}, aliases={})
    candidates = [
        _used_cand("olx", f"u{i}", f"Waterpik model {i} ирригатор", 10000 + i) for i in range(6)
    ]

    listings = notable_used_listings(
        candidates, book, budget=100000, exclude=set(), category="ирригатор"
    )

    assert len(listings) == 3  # _NOTABLE_USED_TOP_N


def test_notable_used_listings_skips_entirely_without_budget():
    """M6: no budget -> no standalone used section at all (no price-sanity
    floor to bound "notable" by)."""
    book = BrandBook(tiers={"Waterpik": "A"}, aliases={})
    candidate = _used_cand("olx", "u1", "Waterpik WP-660 ирригатор", 20000)

    listings = notable_used_listings(
        [candidate], book, budget=None, exclude=set(), category="ирригатор"
    )

    assert listings == []


def test_notable_used_listings_excludes_off_category_junk():
    """HIGH-3b: a listing unrelated to the category (e.g. surfaced by a
    degenerate brand-only recall query) must not surface as "notable"."""
    book = BrandBook(tiers={"Waterpik": "A"}, aliases={})
    off_category = _used_cand("olx", "u1", "Waterpik аккумулятор для авто", 20000)

    listings = notable_used_listings(
        [off_category], book, budget=100000, exclude=set(), category="ирригатор"
    )

    assert listings == []


def test_notable_used_listings_excludes_mimic_candidate():
    """M6: counterfeit gate parity with the new-goods pool."""
    book = BrandBook(tiers={"JBL": "A"}, aliases={})
    mimic = _used_cand("olx", "u1", "Наушники J BL Tws300", 5000)

    listings = notable_used_listings(
        [mimic], book, budget=100000, exclude=set(), category="наушники"
    )

    assert listings == []


# --- category-implied required features (hard-filter regression) -------------


def test_category_implied_feature_dropped_from_required():
    # "TWS" is a token of the category itself — it must not survive as a
    # string-match hard filter (Kaspi titles carry no "TWS" for real TWS buds).
    from buysmarter.ranking.models import QueryConstraints
    from buysmarter.services.recommend import _apply_overrides

    constraints = QueryConstraints(search_phrase="TWS наушники", required_features=["TWS", "IPX7"])

    result = _apply_overrides(constraints, None, None, False)

    assert result.required_features == ["IPX7"]


def test_non_category_features_kept_intact():
    from buysmarter.ranking.models import QueryConstraints
    from buysmarter.services.recommend import _apply_overrides

    constraints = QueryConstraints(search_phrase="ирригатор", required_features=["IPX7"])

    result = _apply_overrides(constraints, "Алматы", 40_000, False)

    assert result.required_features == ["IPX7"]
    assert result.city == "Алматы"
    assert result.budget_max == 40_000


# --- Per-item specs failures must not drop a whole source --------------------


class _FlakySpecsSource:
    """Two search hits; specs for ``bad`` raises (browser hiccup / detail 404)."""

    def __init__(self, name: str, specs_ok: dict[str, str]) -> None:
        self.name = name
        self._specs_ok = specs_ok

    async def search(self, query: str, city: str | None = None) -> list[RawProduct]:
        return [
            RawProduct(source=self.name, product_id="bad", title="Наушники Sony WH-1", price=9000),
            RawProduct(source=self.name, product_id="ok", title="Наушники Sony WH-2", price=8000),
        ]

    async def specs(self, product_id: str) -> dict[str, str]:
        if product_id == "bad":
            raise RuntimeError("navigation timeout")
        return dict(self._specs_ok)


async def test_collect_ozon_one_failing_specs_fetch_keeps_other_candidates() -> None:
    from buysmarter.services.collect import _collect_ozon

    candidates, rows = await _collect_ozon(_FlakySpecsSource("ozon", {}), "наушники", None, None)

    assert {c.source_product_id for c in candidates} == {"bad", "ok"}
    assert len(rows) == 2


async def test_collect_olx_one_failing_specs_fetch_keeps_other_listings() -> None:
    source = _FlakySpecsSource("olx", {"condition": "б/у"})

    used = await _collect_olx(source, "наушники", None, None)

    assert [c.source_product_id for c in used] == ["ok"]


def test_city_id_is_case_insensitive() -> None:
    from buysmarter.services.collect import kaspi_city_id

    assert kaspi_city_id(" астана ") == "710000000"
    assert kaspi_city_id("ASTANA") == "710000000"
    assert kaspi_city_id(None) is None


def test_kaspi_knows_cities_beyond_almaty_and_astana() -> None:
    from buysmarter.services.collect import kaspi_city_id

    assert kaspi_city_id("Шымкент") == "511010000"


async def test_olx_and_kaspi_search_the_query_city() -> None:
    from buysmarter.services.collect import gather_sources

    seen: dict[str, str | None] = {}

    class _CitySource(FakeSource):
        async def search(self, query: str, city: str | None = None) -> list[RawProduct]:
            seen[self.name] = city
            return []

    active = {"kaspi": _CitySource("kaspi", []), "olx": _CitySource("olx", [])}
    await gather_sources(active, "наушники", "Астана", None, None)

    assert seen == {"kaspi": "710000000", "olx": "87"}


# --- review stats + defect-complaint penalty in the review pass -------------


class _StatsKaspi:
    """Kaspi double whose reviews come with aggregate stats (one call)."""

    name = "kaspi"

    def __init__(self, reviews_by_pid: dict[str, list], stats_by_pid: dict) -> None:
        self._reviews = reviews_by_pid
        self._stats = stats_by_pid

    async def reviews_with_stats(self, product_id: str):
        return self._reviews.get(product_id, []), self._stats.get(product_id)


async def test_fetch_top_reviews_collects_stats_when_source_provides_them():
    from market_sources.base import RawReviewStats

    stats = RawReviewStats(source="kaspi", product_id="k1", rating_counts={5: 3}, total=3)
    kaspi = _StatsKaspi({"k1": _reviews("k1", 4)}, {"k1": stats})
    recommendation = assemble_recommendation([_scored("k1", 90.0)], 0)

    reviews_by_id, stats_by_id, _all = await review_mod._fetch_top_reviews(kaspi, recommendation)

    assert len(reviews_by_id[("kaspi", "k1")]) == 4
    assert stats_by_id[("kaspi", "k1")] is stats


async def test_fetch_top_reviews_without_stats_api_yields_no_stats():
    kaspi = _MultiReviewKaspi({"k1": _reviews("k1", 4)})
    recommendation = assemble_recommendation([_scored("k1", 90.0)], 0)

    reviews_by_id, stats_by_id, _all = await review_mod._fetch_top_reviews(kaspi, recommendation)

    assert len(reviews_by_id[("kaspi", "k1")]) == 4
    assert stats_by_id == {}


def test_apply_review_trust_penalizes_defect_complaints() -> None:
    from market_sources.base import RawReview

    defective = [
        RawReview(source="kaspi", product_id="k1", text="пришёл брак, не работает")
        for _ in range(3)
    ] + [RawReview(source="kaspi", product_id="k1", text="подробный честный отзыв номер 1")] * 6
    recommendation = assemble_recommendation([_scored("k1", 90.0)], 0)

    out = review_mod._apply_review_trust(recommendation, {("kaspi", "k1"): defective})

    assert out.best.total_score == round(90.0 * 0.9, 2)
    assert any("жалобы на брак" in con for con in out.best.cons)


# --- B2/B6: required features reach the summary and the no-LLM fallback ------


async def test_summarize_top_passes_only_unverified_required_features(monkeypatch):
    seen: dict[str, list[str]] = {}

    async def _fake_summarize(reviews, features=None):
        seen[reviews[0].product_id] = list(features or [])
        return ReviewSummary()

    monkeypatch.setattr(review_mod, "summarize_reviews", _fake_summarize)

    kaspi = _MultiReviewKaspi({"k1": _reviews("k1", 6)})
    recommendation = assemble_recommendation([_scored("k1", 90.0)], 0)
    reviews_by_id = {("kaspi", "k1"): _reviews("k1", 6)}

    await review_mod._summarize_top(
        recommendation, {"kaspi": kaspi}, reviews_by_id, required_features=["IPX7", "NFC"]
    )

    assert seen == {"k1": ["IPX7", "NFC"]}  # title "product k1" has neither


async def test_review_pass_naive_fallback_confirms_feature_without_llm(monkeypatch):
    from market_sources.base import RawReview

    async def _boom(*args, **kwargs):
        raise AssertionError("no LLM call when use_llm is False")

    monkeypatch.setattr(review_mod, "summarize_reviews", _boom)
    reviews = [
        RawReview(source="kaspi", product_id="k1", text="шумоподавление очень удобно работает"),
        RawReview(source="kaspi", product_id="k1", text="шумоподавление быстро включается"),
    ]
    kaspi = _MultiReviewKaspi({"k1": reviews})
    recommendation = assemble_recommendation([_scored("k1", 90.0)], 0)

    out, summary, _note = await review_mod.review_pass(
        recommendation,
        {"kaspi": kaspi},
        use_llm=False,
        with_review_summary=False,
        required_features=["шумоподавление"],
    )

    assert summary is None
    [top] = out.ranked
    assert top.pros == [
        "отзывы подтверждают: шумоподавление — «шумоподавление очень удобно работает»"
    ]


# --- Defects the seller admits to in the ad text ------------------------------


def test_a_defect_from_the_ad_text_lands_in_the_used_listing_con() -> None:
    new_ref = _cand("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", "Waterpik", 30000)
    used = _cand("olx", "u1", "Waterpik WP-100 ирригатор", "Waterpik", 15000)
    used.specs["description"] = "Корпус треснут, но работает"
    clean = _cand("olx", "u2", "Waterpik WP-100 ирригатор б/у", "Waterpik", 14000)
    clean.specs["description"] = "Без трещин, ремонта не было"

    listings = match_used_listings([new_ref], [used, clean], None)

    con = {listing.title: listing.con for listing in listings}
    assert con[used.title] == "без гарантии/проверки; ⚠ трещины/сколы"
    assert con[clean.title] == "без гарантии/проверки"


def test_a_notable_used_listing_carries_its_defects_too() -> None:
    book = BrandBook(tiers={"Waterpik": "A"}, aliases={})
    used = _used_cand("olx", "u1", "Waterpik WP-660 ирригатор", 20000)
    used.specs["description"] = "Не включается"

    [listing] = notable_used_listings(
        [used], book, budget=100000, exclude=set(), category="ирригатор"
    )

    assert listing.con.endswith("⚠ заявлена неисправность")


async def test_collect_olx_drops_listings_sold_for_parts() -> None:
    class _PartsOlxSource:
        name = "olx"

        async def search(self, query: str, city: str | None = None):
            return [
                RawProduct(source="olx", product_id="p1", title="Наушники Sony на запчасти"),
                RawProduct(source="olx", product_id="p2", title="Наушники Sony WH-2"),
                RawProduct(source="olx", product_id="p3", title="Наушники Sony WH-3"),
            ]

        async def specs(self, product_id: str):
            description = "Продаю на детали" if product_id == "p3" else "Всё работает"
            return {"condition": "б/у", "description": description}

    used = await _collect_olx(_PartsOlxSource(), "наушники", None, None)

    assert [c.source_product_id for c in used] == ["p2"]


# --- unbranded_used_listings: no-name pick -> no-name used under it -----------


def test_unbranded_used_listings_closest_cheaper_first_when_pick_is_noname():
    book = BrandBook(tiers={"Yamaha": "A"}, aliases={})
    pick = _used_cand("kaspi", "k1", "Гитара акустическая TUKKA TS-38", 16000)
    near = _used_cand("olx", "u1", "Гитара акустическая", 11000)
    cheap = _used_cand("olx", "u2", "Гитара классическая", 5000)
    too_close = _used_cand("olx", "u3", "Гитара акустика черная", 14000)  # not 30% cheaper
    branded = _used_cand("olx", "u4", "Гитара Yamaha", 9000)  # notable section's job

    listings = unbranded_used_listings(
        [cheap, near, too_close, branded], book, pick, exclude=set(), category="гитара"
    )

    assert [listing.title for listing in listings] == [near.title, cheap.title]
    assert listings[0].saving == 5000 and listings[0].matched_title == pick.title


def test_unbranded_used_listings_empty_when_pick_is_branded():
    book = BrandBook(tiers={"Yamaha": "A"}, aliases={})
    pick = _used_cand("kaspi", "k1", "Гитара Yamaha C40", 40000)
    noname = _used_cand("olx", "u1", "Гитара акустическая", 10000)

    assert unbranded_used_listings([noname], book, pick, exclude=set(), category="гитара") == []


def test_unbranded_used_listings_prefers_new_ref_sharing_latin_name():
    book = BrandBook(tiers={}, aliases={})
    pick = _used_cand("kaspi", "k1", "Гитара акустическая TUKKA TS-38", 16000)
    same_name = _used_cand("kaspi", "k2", "Гитара акустическая Levinson LA-R38B", 22000)
    used = _used_cand("olx", "u1", "Акустическая гитара Levinson", 10000)

    [listing] = unbranded_used_listings(
        [used], book, pick, exclude=set(), category="гитара", refs=[pick, same_name]
    )

    assert listing.matched_title == same_name.title and listing.saving == 12000


async def test_collect_ozon_passes_budget_to_search() -> None:
    from buysmarter.services.collect import _collect_ozon

    class _BudgetSource(_FlakySpecsSource):
        async def search(self, query: str, city: str | None = None, budget: int | None = None):
            self.budget = budget
            return []

    source = _BudgetSource("ozon", {})
    await _collect_ozon(source, "гитара", None, 30000)

    assert source.budget == 30000


async def test_collect_ozon_drops_off_category_cards() -> None:
    from buysmarter.services.collect import _collect_ozon

    class _MixedSource(_FlakySpecsSource):
        async def search(self, query: str, city: str | None = None) -> list[RawProduct]:
            return [
                RawProduct(source="ozon", product_id="g", title="Гитара акустическая", price=20000),
                RawProduct(source="ozon", product_id="u", title="Укулеле SZRU-OZ3P", price=1616),
            ]

    candidates, rows = await _collect_ozon(_MixedSource("ozon", {}), "гитара", None, None)

    assert [c.source_product_id for c in candidates] == ["g"]
    assert len(rows) == 1


async def test_without_any_llm_route_the_run_goes_keyless_and_says_what_it_skipped(
    stub_source: StubKaspiSource,
) -> None:
    # conftest: no claude CLI, an empty keyring, and these settings carry no env key
    result = await recommend(
        "нужен ирригатор, портативный, до 40000 тг, Алматы", source=stub_source, persist=False
    )

    assert result.recommendation.ranked
    assert result.skipped_llm_steps == [
        "query parsing",
        "review summaries",
        "ingredient extraction",
    ]


async def test_an_explicit_no_llm_run_lists_no_skipped_steps(stub_source: StubKaspiSource) -> None:
    result = await recommend("ирригатор до 40000", use_llm=False, source=stub_source, persist=False)
    assert result.skipped_llm_steps == []


async def test_review_pass_says_why_the_best_pick_has_no_summary(monkeypatch) -> None:
    async def _never(reviews, features=None):
        raise AssertionError("too few reviews: the LLM must not be asked")

    monkeypatch.setattr(review_mod, "summarize_reviews", _never)
    kaspi = _MultiReviewKaspi({"k1": _reviews("k1", 2)})
    recommendation = assemble_recommendation([_scored("k1", 90.0)], 0)

    _out, summary, note = await review_mod.review_pass(
        recommendation, {"kaspi": kaspi}, use_llm=True, with_review_summary=True
    )

    assert summary is None
    assert note is not None and "мало текстов отзывов" in note


async def test_nothing_new_in_budget_still_shows_the_olx_ads_within_it() -> None:
    kaspi = FakeSource(
        "kaspi", [_rp("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", 60000)]
    )
    olx = FakeSource(
        "olx",
        [
            _rp("olx", "u1", "Waterpik WP-100 ирригатор портативный", 15000),
            _rp("olx", "u2", "Waterpik WP-100 ирригатор портативный новый", 45000),
        ],
        specs_map={"u1": {"condition": "Б/у"}, "u2": {"condition": "Б/у"}},
    )

    with pytest.raises(NoNewPick) as caught:
        await recommend(
            "ирригатор портативный, до 40000 тг",
            use_llm=False,
            sources=["kaspi", "olx"],
            source=kaspi,
            olx_source=olx,
            persist=False,
        )

    no_pick = caught.value
    assert no_pick.rejected_reasons == {"price exceeds budget": 1}
    assert [(u.price, u.saving) for u in no_pick.used_listings] == [(15000, 45000)]  # vs new 60000
    assert no_new_pick_text(no_pick.rejected_reasons, bool(no_pick.used_listings)) == (
        "Новых предложений нет: дороже бюджета ×1. На OLX нашлось:"
    )
    assert isinstance(no_pick, ValueError)


async def test_nothing_new_without_olx_in_the_search_does_not_speak_of_olx() -> None:
    from buysmarter.cli.report import no_new_pick_message

    kaspi = FakeSource(
        "kaspi", [_rp("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", 60000)]
    )

    with pytest.raises(NoNewPick) as caught:
        await recommend(
            "ирригатор портативный, до 40000 тг",
            use_llm=False,
            sources=["kaspi"],
            source=kaspi,
            persist=False,
        )

    assert caught.value.olx_searched is False
    assert no_new_pick_message(caught.value) == "Новых предложений нет: дороже бюджета ×1."


def _ads(*prices: int) -> list[Candidate]:
    return [
        Candidate(source="olx", source_product_id=str(i), title=f"iPhone 13 128GB #{i}", price=p)
        for i, p in enumerate(prices)
    ]


async def test_an_olx_ad_far_below_the_others_is_dropped_as_bait() -> None:
    constraints = QueryConstraints(search_phrase="смартфон iPhone 13")
    ads = _ads(120_000, 125_000, 126_000, 130_000, 118_000, 35_000)

    kept = await _filter_used(ads, constraints, use_llm=False)

    assert sorted(a.price for a in kept) == [118_000, 120_000, 125_000, 126_000, 130_000]


async def test_too_few_olx_ads_for_a_median_keep_every_price() -> None:
    constraints = QueryConstraints(search_phrase="смартфон iPhone 13")

    kept = await _filter_used(_ads(120_000, 125_000, 130_000, 35_000), constraints, use_llm=False)

    assert len(kept) == 4


# --- Model numbers and the honest saving ------------------------------------------


def test_a_used_ad_for_another_model_number_is_not_matched() -> None:
    new_13 = _cand("kaspi", "k1", "Смартфон Apple iPhone 13 128Gb Dual Sim синий", "Apple", 373000)
    used_12 = _cand("olx", "u1", "Apple iPhone 12", "Apple", 130000)
    used_13 = _cand("olx", "u2", "iPhone 13 128GB Б/У", "Apple", 125000)

    listings = match_used_listings([new_13], [used_12, used_13], None)

    assert [listing.title for listing in listings] == ["iPhone 13 128GB Б/У"]


def test_a_new_listing_for_another_model_number_is_not_merged() -> None:
    a = _cand("kaspi", "k1", "Apple iPhone 12 128Gb черный", "Apple", 300000)
    b = _cand("ozon", "o1", "Apple iPhone 13 128Gb черный", "Apple", 370000)

    kept, _ = dedupe_cross_source([a, b])

    assert len(kept) == 2


def test_the_saving_is_counted_against_the_cheapest_matching_new_one() -> None:
    cheap = _cand("kaspi", "k1", "Смартфон Apple iPhone 13 128Gb NanoSIM черный", "Apple", 373000)
    dear = _cand("kaspi", "k2", "Смартфон Apple iPhone 13 128Gb Dual Sim синий", "Apple", 529990)
    used = _cand("olx", "u1", "iPhone 13 128GB Б/У", "Apple", 125000)

    [listing] = match_used_listings([cheap, dear], [used], None)

    assert (listing.new_price, listing.saving) == (373000, 248000)


def test_a_notable_ad_for_another_model_than_the_query_names_is_left_out() -> None:
    book = BrandBook(tiers={"Apple": "A"}, aliases={})
    used_12 = _cand("olx", "u1", "Apple iPhone 12", "Apple", 130000)
    used_13 = _cand("olx", "u2", "Apple iPhone 13", "Apple", 196000)

    listings = notable_used_listings(
        [used_12, used_13], book, budget=250000, exclude=set(), category="смартфон iPhone 13"
    )

    assert [listing.title for listing in listings] == ["Apple iPhone 13"]


def test_a_used_ad_with_shop_words_matches_on_its_model_number() -> None:
    new_13 = _cand("kaspi", "k1", "Apple iPhone 13 128Gb черный", "Apple", 373000)
    used = _cand("olx", "u1", "iphone 13 Shark Store", "Apple", 180000)

    [listing] = match_used_listings([new_13], [used], None)

    assert listing.matched_title == new_13.title


def test_an_unbranded_ad_for_another_model_number_is_left_out() -> None:
    book = BrandBook(tiers={}, aliases={})
    pick = _used_cand("kaspi", "k1", "Наушники TWS Pro 6", 20000)
    same = _used_cand("olx", "u1", "Наушники TWS Pro 6", 9000)
    other = _used_cand("olx", "u2", "Наушники TWS Pro 4", 8000)

    listings = unbranded_used_listings(
        [same, other], book, pick, exclude=set(), category="наушники TWS Pro 6"
    )

    assert [listing.title for listing in listings] == [same.title]


def test_the_saving_is_counted_against_the_new_one_with_the_same_storage() -> None:
    new_128 = _cand("kaspi", "k1", "Смартфон Apple iPhone 13 128Gb черный", "Apple", 373000)
    new_512 = _cand("kaspi", "k2", "Смартфон Apple iPhone 13 512Gb Dual Sim синий", "Apple", 529990)
    used_128 = _cand("olx", "u1", "Iphone 13 128 gb midnight", "Apple", 150000)
    used_256 = _cand("olx", "u2", "iPhone 13 4/256 отличное состояние", "Apple", 170000)

    listings = match_used_listings([new_512, new_128], [used_128, used_256], None)

    assert [(listing.title, listing.new_price) for listing in listings] == [
        ("Iphone 13 128 gb midnight", 373000)
    ]


def test_new_listings_with_different_storage_are_not_merged() -> None:
    a = _cand("kaspi", "k1", "Apple iPhone 13 128Gb черный", "Apple", 373000)
    b = _cand("ozon", "o1", "Apple iPhone 13 256Gb черный", "Apple", 420000)

    kept, _ = dedupe_cross_source([a, b])

    assert len(kept) == 2


def test_a_line_variant_without_a_spaced_model_number_is_not_merged() -> None:
    plain = _cand("kaspi", "k1", "Samsung Galaxy S24 8/256Gb черный", "Samsung", 400000)
    ultra = _cand("ozon", "o1", "Samsung Galaxy S24 Ultra 8/256Gb черный", "Samsung", 600000)

    kept, _ = dedupe_cross_source([plain, ultra])

    assert len(kept) == 2


def test_a_pro_and_its_plus_are_not_merged() -> None:
    plain = _cand("kaspi", "k1", "Xiaomi Redmi Note 13 8/256Gb черный", "Xiaomi", 110000)
    pro = _cand("ozon", "o1", "Xiaomi Redmi Note 13 Pro 8/256Gb черный", "Xiaomi", 150000)
    pro_plus = _cand("ozon", "o2", "Xiaomi Redmi Note 13 Pro+ 8/256Gb черный", "Xiaomi", 190000)

    kept, _ = dedupe_cross_source([plain, pro, pro_plus])

    assert len(kept) == 3


def test_ram_plus_storage_is_not_a_plus_model() -> None:
    a = _cand("kaspi", "k1", "Xiaomi Redmi Note 13 8+256GB черный", "Xiaomi", 110000)
    b = _cand("ozon", "o1", "Xiaomi Redmi Note 13 8/256GB черный", "Xiaomi", 112000)

    kept, _ = dedupe_cross_source([a, b])

    assert len(kept) == 1


def test_two_cards_inside_one_source_are_not_merged() -> None:
    first = _cand("kaspi", "k1", "Vivo TWS Air 3 наушники", "Vivo", 3_208)
    second = _cand("kaspi", "k2", "Vivo TWS Air 3 наушники", "Vivo", 11_186)

    kept, notes = dedupe_cross_source([first, second])

    assert [c.source_product_id for c in kept] == ["k1", "k2"]
    assert all(c.dup_price_gap is None for c in kept)
    assert notes == {}


def test_a_second_card_from_a_source_already_in_the_group_is_not_merged() -> None:
    kaspi = _cand("kaspi", "k1", "Waterpik WP-100 ирригатор", "Waterpik", 30000)
    ozon = _cand("ozon", "o1", "Waterpik WP-100 ирригатор", "Waterpik", 25000)
    kaspi_again = _cand("kaspi", "k2", "Waterpik WP-100 ирригатор", "Waterpik", 28000)

    kept, _ = dedupe_cross_source([kaspi, ozon, kaspi_again])

    assert sorted(c.source_product_id for c in kept) == ["k2", "o1"]


def test_an_official_store_item_still_merges_with_its_kaspi_card() -> None:
    kaspi = _cand("kaspi", "k1", "Waterpik WP-100 ирригатор", "Waterpik", 30000)
    official = _cand("official", "s1", "Waterpik WP-100 ирригатор", "Waterpik", 32000)

    kept, notes = dedupe_cross_source([kaspi, official])

    assert [c.source for c in kept] == ["kaspi"]
    assert notes[("kaspi", "k1")].text.startswith("также на official")


def test_a_used_pro_is_not_matched_to_the_plain_model() -> None:
    plain = _cand("kaspi", "k1", "Смартфон Apple iPhone 13 128Gb черный", "Apple", 288535)
    used_pro = _cand("olx", "u1", "IPhone 13 pro в отличном состоянии", "Apple", 190000)
    used_pro_max = _cand("olx", "u2", "iPhone 13 Pro Max 128 ГБ Sierra Blue", "Apple", 199990)
    used_plain = _cand("olx", "u3", "iphone 13 Shark Store", "Apple", 165000)

    listings = match_used_listings([plain], [used_pro, used_pro_max, used_plain], None)

    assert [listing.title for listing in listings] == ["iphone 13 Shark Store"]


async def test_a_marketplace_that_failed_is_named_in_the_result(
    stub_source: StubKaspiSource, monkeypatch
) -> None:
    from buysmarter.services import recommend as recommend_module

    real = recommend_module.gather_sources

    async def ozon_down(*args, **kwargs):
        return {**await real(*args, **kwargs), "ozon": None}  # what _safe gives a dead source

    monkeypatch.setattr(recommend_module, "gather_sources", ozon_down)
    result = await recommend(
        "ирригатор портативный до 40000 тг", use_llm=False, source=stub_source, persist=False
    )
    assert result.failed_sources == ["ozon"]
