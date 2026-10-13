"""Parse functions against real saved Kaspi.kz responses. No network."""

import json
from pathlib import Path

import pytest
from market_sources.kaspi.parse import parse_offers, parse_reviews, parse_search, parse_specs

SAMPLES_DIR = Path(__file__).parent.parent / "samples" / "kaspi"
PRODUCT_ID = "106935673"


def _load(name: str) -> dict:
    return json.loads((SAMPLES_DIR / f"{name}.json").read_text())


@pytest.fixture
def search_payload() -> dict:
    return _load("search")


@pytest.fixture
def offers_payload() -> dict:
    return _load("offers")


@pytest.fixture
def reviews_payload() -> dict:
    return _load("reviews")


@pytest.fixture
def specs_payload() -> dict:
    return _load("specs")


def test_parse_search_returns_all_cards(search_payload):
    products = parse_search(search_payload)
    assert len(products) == len(search_payload["data"]["cards"])


def test_parse_search_extracts_core_fields(search_payload):
    products = parse_search(search_payload)
    first = products[0]
    assert first.source == "kaspi"
    assert first.product_id == PRODUCT_ID
    assert first.title.startswith("Aulandi")
    assert first.price == 15900
    assert first.rating == 4.9
    assert first.review_count == 1375


def test_parse_search_builds_public_shop_url(search_payload):
    products = parse_search(search_payload)
    first = products[0]
    assert first.url is not None
    assert first.url.startswith("https://kaspi.kz/shop/p/")


def test_parse_search_skips_cards_without_id_or_title():
    payload = {"data": {"cards": [{"title": "no id"}, {"id": "1"}]}}
    assert parse_search(payload) == []


def test_parse_search_strips_variant_letter_suffix_from_id():
    """Fashion cards carry a variant-letter-suffixed id, e.g. "16540138b"."""
    payload = {
        "data": {
            "cards": [
                {
                    "id": "16540138b",
                    "title": "Кроссовки серый",
                    "shopLink": "/p/krossovki-seryi-16540138b/",
                }
            ]
        }
    }
    products = parse_search(payload)
    assert len(products) == 1
    assert products[0].product_id == "16540138"


def test_parse_search_marks_specs_unavailable_for_variant_letter_suffix():
    """A stripped variant-letter id (fashion card) has no content-API specs page."""
    payload = {
        "data": {
            "cards": [
                {
                    "id": "16540138b",
                    "title": "Кроссовки серый",
                    "shopLink": "/p/krossovki-seryi-16540138b/",
                }
            ]
        }
    }
    products = parse_search(payload)
    assert products[0].specs_available is False


def test_parse_search_keeps_specs_available_for_plain_numeric_id(search_payload):
    """Ordinary numeric-id cards (non-fashion) are unaffected by the fix."""
    products = parse_search(search_payload)
    assert all(p.specs_available for p in products)


def test_parse_search_falls_back_to_url_slug_when_id_has_no_digits():
    payload = {
        "data": {
            "cards": [
                {
                    "id": "abc",
                    "title": "Кроссовки серый",
                    "shopLink": "/p/krossovki-seryi-16540138b/",
                }
            ]
        }
    }
    products = parse_search(payload)
    assert len(products) == 1
    assert products[0].product_id == "16540138"


def test_parse_search_skips_card_with_no_numeric_id_anywhere():
    payload = {
        "data": {
            "cards": [
                {"id": "abc", "title": "no digits", "shopLink": "/p/no-digits-here/"},
            ]
        }
    }
    assert parse_search(payload) == []


def test_parse_offers_returns_all_offers(offers_payload):
    offers = parse_offers(offers_payload, PRODUCT_ID)
    assert len(offers) == len(offers_payload["offers"])


def test_parse_offers_extracts_merchant_and_price(offers_payload):
    offers = parse_offers(offers_payload, PRODUCT_ID)
    first = offers[0]
    assert first.source == "kaspi"
    assert first.product_id == PRODUCT_ID
    assert first.seller == "Aulandi"
    assert first.price == 15900.0
    assert first.merchant_id == "15587089"
    assert first.merchant_rating == 4.9
    assert first.merchant_review_count == 1591
    assert first.delivery == "TOMORROW"


def test_parse_reviews_returns_all_reviews(reviews_payload):
    reviews = parse_reviews(reviews_payload, PRODUCT_ID)
    assert len(reviews) == len(reviews_payload["data"])


def test_parse_reviews_extracts_author_rating_text(reviews_payload):
    reviews = parse_reviews(reviews_payload, PRODUCT_ID)
    first = reviews[0]
    assert first.source == "kaspi"
    assert first.product_id == PRODUCT_ID
    assert first.author == "Виктория"
    assert first.rating == 5
    assert "Ирригатор хороший" in first.text
    assert first.created_at == "13.05.2023"


def test_parse_specs_returns_flat_name_value_dict(specs_payload):
    specs = parse_specs(specs_payload)
    assert specs["Тип"] == "ирригатор"
    assert specs["Принцип работы"] == "импульсный"
    assert all(isinstance(v, str) for v in specs.values())


def test_parse_specs_skips_invisible_features():
    payload = {
        "specifications": [
            {
                "name": "Group",
                "features": [
                    {"name": "Hidden", "visible": False, "featureValues": [{"value": "x"}]},
                    {"name": "Shown", "visible": True, "featureValues": [{"value": "y"}]},
                ],
            }
        ]
    }
    specs = parse_specs(payload)
    assert "Hidden" not in specs
    assert specs["Shown"] == "y"
