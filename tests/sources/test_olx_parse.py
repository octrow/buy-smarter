"""Parse functions against real saved OLX.kz responses. No network."""

import json
from pathlib import Path

import pytest
from market_sources.olx.parse import parse_offer, parse_search, parse_specs

SAMPLES_DIR = Path(__file__).parent.parent / "samples" / "olx"


def _load(name: str) -> dict:
    return json.loads((SAMPLES_DIR / f"{name}.json").read_text())


@pytest.fixture
def search_payload() -> dict:
    return _load("search")


@pytest.fixture
def detail_offer() -> dict:
    return _load("detail")["data"]


def test_parse_search_returns_all_cards(search_payload):
    products = parse_search(search_payload)
    assert len(products) == len(search_payload["data"])


def test_parse_search_extracts_core_fields(search_payload):
    products = parse_search(search_payload)
    waterpik = next(p for p in products if p.product_id == "380868859")
    assert waterpik.source == "olx"
    assert waterpik.title.startswith("Ирригатор")
    assert waterpik.price == 20000
    assert waterpik.currency == "KZT"
    assert waterpik.url.startswith("https://www.olx.kz/")


def test_parse_search_has_no_ratings(search_payload):
    # OLX carries no product reviews/ratings.
    for product in parse_search(search_payload):
        assert product.rating is None
        assert product.review_count is None


def test_parse_search_price_absent_is_none(search_payload):
    products = parse_search(search_payload)
    no_price = next(p for p in products if p.product_id == "397121329")
    assert no_price.price is None


def test_parse_search_image_url_substitutes_dimensions(search_payload):
    products = parse_search(search_payload)
    used = next(p for p in products if p.product_id == "398822791")
    assert used.image_url is not None
    assert "{width}" not in used.image_url
    assert "{height}" not in used.image_url


def test_parse_search_skips_offers_without_id_or_title():
    payload = {"data": [{"title": "no id"}, {"id": 1}]}
    assert parse_search(payload) == []


def test_parse_offer_builds_single_offer(detail_offer):
    offers = parse_offer(detail_offer)
    assert len(offers) == 1
    offer = offers[0]
    assert offer.source == "olx"
    assert offer.product_id == "380868859"
    assert offer.price == 20000.0
    assert offer.seller == "ahmedisagyngali"
    assert offer.city == "Каргалы"
    assert offer.merchant_id == "351679374"


def test_parse_offer_empty_when_no_price():
    assert parse_offer({"id": 1, "title": "x", "params": []}) == []


def test_parse_specs_extracts_condition_new(detail_offer):
    specs = parse_specs(detail_offer)
    assert specs["condition"] == "Новый"
    assert specs["seller_type"] == "private"
    assert specs["city"] == "Каргалы"


def test_parse_specs_used_and_business(search_payload):
    by_id = {o["id"]: o for o in search_payload["data"]}
    used = parse_specs(by_id[398822791])
    assert used["condition"] == "Б/у"
    assert used["seller_type"] == "private"

    business = parse_specs(by_id[397121329])
    assert business["seller_type"] == "business"
    assert "condition" not in business  # service ad, no state param


def test_parse_specs_always_has_seller_type():
    assert parse_specs({"id": 1})["seller_type"] == "private"
