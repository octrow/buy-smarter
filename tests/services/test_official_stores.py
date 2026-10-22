"""Official brand stores: JSON-LD parsing, price floors, candidates. No network."""

import json

from buysmarter.knowledge.brands import BrandBook
from buysmarter.ranking.audience import other_audience
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services import official_stores as stores
from buysmarter.services.official_stores import StoreOffer, parse_store_offers


def _page(*products: dict) -> str:
    item_list = {
        "@context": "https://schema.org/",
        "@type": "ItemList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "item": p} for i, p in enumerate(products, 1)
        ],
    }
    return (
        '<script type="application/ld+json">{"@type": "OnlineStore"}</script>'
        '<script type="application/ld+json">not json</script>'
        f'<script type="application/ld+json">{json.dumps(item_list)}</script>'
    )


def _product(name: str, price, currency: str = "KZT", availability: str = "InStock") -> dict:
    return {
        "@type": "Product",
        "name": name,
        "url": f"https://puma.kz/{name}",
        "offers": {
            "@type": "Offer",
            "priceCurrency": currency,
            "price": price,
            "availability": f"https://schema.org/{availability}",
        },
    }


def test_product_links_are_resolved_and_non_https_dropped():
    html = _page(_product("Rel", 1000), _product("Js", 1000))
    html = html.replace("https://puma.kz/Rel", "/rel").replace("https://puma.kz/Js", "javascript:x")

    offers = parse_store_offers(html, "https://puma.kz/men")

    assert [o.url for o in offers] == ["https://puma.kz/rel", None]


def test_an_overflowing_price_is_skipped_not_a_crash():
    assert parse_store_offers(_page(_product("Inf", "1e999"))) == []


async def test_only_public_https_urls_are_fetched():
    assert not await stores._is_public_https("http://puma.kz/x")
    assert not await stores._is_public_https("https://127.0.0.1/x")
    assert not await stores._is_public_https("https://169.254.169.254/latest")


def test_book_keeps_only_https_store_links():
    book = BrandBook(official_stores={"A": "https://a.kz", "B": "http://10.0.0.1:6379/"})

    assert book.official_stores == {"A": "https://a.kz"}


def test_parse_store_offers_reads_item_list_products_in_kzt():
    html = _page(
        _product("Cell Thrill", 13000),
        _product("Scend Pro", "18 000"),
        _product("Euro only", 99, currency="EUR"),
        _product("Gone", 9000, availability="OutOfStock"),
    )

    offers = parse_store_offers(html)

    assert [(o.name, o.price, o.in_stock) for o in offers] == [
        ("Cell Thrill", 13_000, True),
        ("Scend Pro", 18_000, True),
        ("Gone", 9_000, False),
    ]


_BOOK = BrandBook(
    tiers={"Puma": "B", "Adidas": "A", "Demix": "C"},
    official_stores={
        "Puma": "https://puma.kz/men",
        "Adidas": "https://adidas.kz/blocked",
        "Demix": "https://demix.kz/x",
    },
)


async def _fake_fetch_all(targets):
    pages = {
        "https://puma.kz/men": [
            StoreOffer(name="Smash", price=22_000),
            StoreOffer(name="Cell Thrill", price=13_000),
            StoreOffer(name="Suede", price=40_000),
            StoreOffer(name="Кроссовки детские Kids", price=9_000),
        ],
    }
    return [(brand, url, pages.get(url, [])) for brand, url in targets]


async def test_verify_store_floors_keeps_only_stores_that_answered(monkeypatch):
    monkeypatch.setattr(stores, "_fetch_all", _fake_fetch_all)

    book = await stores.verify_store_floors(_BOOK, "мужские кроссовки")

    assert book.price_floors == {"Puma": 13_000}
    assert book.official_stores == {"Puma": "https://puma.kz/men", "Demix": "https://demix.kz/x"}


async def test_official_candidates_are_the_cheapest_in_budget_trusted_products(monkeypatch):
    monkeypatch.setattr(stores, "_fetch_all", _fake_fetch_all)

    candidates = await stores.official_candidates(
        _BOOK,
        QueryConstraints(search_phrase="мужские кроссовки", budget_max=35_000),
        lambda name: other_audience("male", name),
    )

    assert [(c.title, c.price) for c in candidates] == [
        ("Puma Cell Thrill", 13_000),
        ("Puma Smash", 22_000),
    ]
    assert all(c.source == "official" and c.official_seller for c in candidates)
    assert candidates[0].brand_tier == "B"
