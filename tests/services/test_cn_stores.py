"""Chinese brand sites: uniqlo.cn's search JSON as official offers in yuan, for the comparison."""

import asyncio
import json
from pathlib import Path

import httpx

from buysmarter.ranking.models import OFFICIAL_CN_SOURCE, QueryConstraints
from buysmarter.services import cn_stores

_SAMPLE = json.loads(
    (Path(__file__).parents[1] / "samples" / "uniqlo_cn" / "search.json").read_text("utf-8")
)


def test_the_search_gives_in_stock_offers_in_yuan_for_the_audience():
    offers = cn_stores.parse_uniqlo(_SAMPLE, gender=None)

    first = offers[0]
    assert first.source == OFFICIAL_CN_SOURCE and first.official_seller
    assert (first.price, first.currency, first.brand) == (149, "CNY", "Uniqlo")
    assert first.title == "Uniqlo 宽松卫衣/长袖 489935"
    assert first.url == "https://www.uniqlo.cn/product-detail.html?productCode=u0000000077316"
    assert first.image_url.startswith("https://www.uniqlo.cn/hmall/")
    assert len(offers) == 3  # the two kids' hoodies are left out of an adult search


def test_a_kids_search_keeps_only_the_kids_offers():
    offers = cn_stores.parse_uniqlo(_SAMPLE, gender="kids")

    assert [o.price for o in offers] == [129, 129]


def _client(bodies: list[dict]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_SAMPLE)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_a_query_naming_uniqlo_searches_its_chinese_site_by_the_chinese_type():
    bodies: list[dict] = []
    query = QueryConstraints(search_phrase="худи uniqlo relaxed", product_type="худи")

    offers = asyncio.run(cn_stores.cn_store_candidates(query, _client(bodies)))

    assert bodies[0]["description"] == "卫衣"
    assert offers and offers[0].currency == "CNY"


def test_no_brand_with_a_chinese_site_or_an_unknown_type_asks_nothing():
    bodies: list[dict] = []
    nike = QueryConstraints(search_phrase="худи nike", product_type="худи")
    pan = QueryConstraints(search_phrase="сковорода uniqlo", product_type="сковорода")

    assert asyncio.run(cn_stores.cn_store_candidates(nike, _client(bodies))) == []
    assert asyncio.run(cn_stores.cn_store_candidates(pan, _client(bodies))) == []
    assert bodies == []


def test_a_blocked_site_gives_nothing_instead_of_failing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(567, text="AccessDeny")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    query = QueryConstraints(search_phrase="худи юникло", product_type="худи")

    assert asyncio.run(cn_stores.cn_store_candidates(query, client)) == []
