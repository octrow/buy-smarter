"""Parse functions against real saved Ozon.kz composer-api responses. No network.

Samples were captured live from ozon.kz during the phase-2 spike
(see ``docs/spikes/ozon-spike.md``): search for "ирригатор", then the top
product's PDP and reviews pages.
"""

import json
from pathlib import Path

import pytest
from market_sources.ozon.parse import (
    parse_offer,
    parse_reviews,
    parse_search,
    parse_specs,
    price_to_int,
)

SAMPLES_DIR = Path(__file__).parent.parent / "samples" / "ozon"
PRODUCT_ID = "1710439815"


def _load(name: str) -> dict:
    return json.loads((SAMPLES_DIR / f"{name}.json").read_text())


@pytest.fixture
def search_payload() -> dict:
    return _load("search")


@pytest.fixture
def product_payload() -> dict:
    return _load("product")


@pytest.fixture
def reviews_payload() -> dict:
    return _load("reviews")


# ── price helper ─────────────────────────────────────────────────────────────
def test_price_to_int_strips_currency_and_spaces():
    assert price_to_int("14 589 ₸") == 14589
    assert price_to_int("45 903 ₸") == 45903


def test_price_to_int_handles_garbage():
    assert price_to_int(None) is None
    assert price_to_int("") is None
    assert price_to_int("нет цены") is None


# ── search ───────────────────────────────────────────────────────────────────
def test_parse_search_returns_products(search_payload):
    products = parse_search(search_payload)
    assert len(products) == 8


def test_parse_search_extracts_core_fields(search_payload):
    first = parse_search(search_payload)[0]
    assert first.source == "ozon"
    assert first.product_id == PRODUCT_ID
    assert first.title.startswith("Ирригатор")
    assert first.currency == "KZT"
    assert first.rating == 4.9
    assert first.review_count == 33836


def test_parse_search_uses_real_price_not_installment(search_payload):
    # tiles carry an installment block ("1 216 ₸ × 12 мес") before the real price
    first = parse_search(search_payload)[0]
    assert first.price == 14589.0


def test_parse_search_builds_absolute_product_url(search_payload):
    first = parse_search(search_payload)[0]
    assert first.url is not None
    assert first.url.startswith("https://www.ozon.kz/product/")
    assert "?" not in first.url  # tracking stripped


def test_parse_search_skips_items_without_sku_or_price():
    payload = {
        "widgetStates": {
            "tileGridDesktop-1-default-1": json.dumps(
                {"items": [{"mainState": [{"id": "name", "textDS": {"text": "no price"}}]}]}
            )
        }
    }
    assert parse_search(payload) == []


# ── offer ────────────────────────────────────────────────────────────────────
def test_parse_offer_single_offer_from_price_and_seller(product_payload):
    offer = parse_offer(product_payload, PRODUCT_ID)
    assert offer is not None
    assert offer.source == "ozon"
    assert offer.product_id == PRODUCT_ID
    assert offer.price == 14589.0
    assert offer.currency == "KZT"
    assert offer.in_stock is True
    assert offer.seller.startswith("PECHAM")
    assert offer.merchant_rating == 4.9


def test_parse_offer_returns_none_without_price_widget():
    assert parse_offer({"widgetStates": {}}, PRODUCT_ID) is None


# ── specs ────────────────────────────────────────────────────────────────────
def test_parse_specs_returns_flat_name_value_dict(product_payload):
    specs = parse_specs(product_payload)
    assert specs["Тип ирригатора"].startswith("Моноструйный")
    assert specs["Емкость контейнера для воды, мл"] == "290"
    assert all(isinstance(v, str) for v in specs.values())
    assert all(not v.endswith(", ") for v in specs.values())  # trailing sep stripped


def test_parse_specs_empty_without_widget():
    assert parse_specs({"widgetStates": {}}) == {}


# ── reviews ──────────────────────────────────────────────────────────────────
def test_parse_reviews_returns_reviews(reviews_payload):
    reviews = parse_reviews(reviews_payload, PRODUCT_ID)
    assert len(reviews) == 30


def test_parse_reviews_extracts_author_rating_text_date(reviews_payload):
    first = parse_reviews(reviews_payload, PRODUCT_ID)[0]
    assert first.source == "ozon"
    assert first.product_id == PRODUCT_ID
    assert first.author == "Алена Х."
    assert first.rating == 5.0
    assert first.text
    assert first.created_at is not None
    assert len(first.created_at) == 10  # ISO YYYY-MM-DD


def test_parse_reviews_empty_without_widget():
    assert parse_reviews({"widgetStates": {}}, PRODUCT_ID) == []
