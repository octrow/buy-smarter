"""Brand sites in China (the same catalog as the brand's WeChat mini-app), for the comparison.

A Chinese site renders in JavaScript, so each one gets its own reader of the JSON its pages
call; one reader per brand lives in ``_STORES``. A site is searched only when the query names
the brand, by the product type's Chinese word. Its offers are priced in yuan and go straight
to the cross-market comparison, not into the ranking: their price is not in tenge.

Best-effort: a blocked or changed site yields nothing, with a warning in the log.
"""

import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from buysmarter.ranking.audience import FEMALE, KIDS, MALE
from buysmarter.ranking.models import OFFICIAL_CN_SOURCE, Candidate, QueryConstraints
from buysmarter.services.official_stores import _HEADERS as BROWSER_HEADERS

logger = logging.getLogger(__name__)

CNY = "CNY"
_TIMEOUT_S = 20.0
_PER_STORE = 3
_PAGE_SIZE = 20

# The product type's word on a Chinese site. ponytail: the clothing types of the weight table;
# add a word when a type misses.
ZH_TYPES = {
    "футболка": "T恤",
    "рубашка": "衬衫",
    "курта": "衬衫",
    "худи": "卫衣",
    "свитшот": "卫衣",
    "джинсы": "牛仔裤",
    "брюки": "长裤",
    "куртка": "夹克",
    "пуховик": "羽绒服",
}

UNIQLO_SEARCH = (
    "https://d.uniqlo.cn/p/hmall-sc-service/search/searchWithDescriptionAndConditions/zh_CN"
)
UNIQLO_SITE = "https://www.uniqlo.cn"
# The site's WAF turns away a client that does not look like its own pages.
_UNIQLO_HEADERS = {
    **BROWSER_HEADERS,
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Origin": UNIQLO_SITE,
    "Referer": f"{UNIQLO_SITE}/",
}
# The audience word of uniqlo.cn's ``sex4zhCN`` ("男装/男女同款", "童装男童女童").
_KIDS_MARKS = ("童", "婴")
_SEX_MARKS = {MALE: "男", FEMALE: "女"}


def _fits(sex: str, gender: str | None) -> bool:
    kids = any(mark in sex for mark in _KIDS_MARKS)
    if gender == KIDS:
        return kids
    if kids:
        return False
    return gender is None or _SEX_MARKS.get(gender, "") in sex


def parse_uniqlo(payload: dict[str, Any], gender: str | None) -> list[Candidate]:
    """In-stock products for the audience, in the site's order, as official offers in yuan."""
    resp = payload.get("resp")
    items = resp[1] if isinstance(resp, list) and len(resp) > 1 else []
    offers = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("stock") != "Y":
            continue
        if not _fits(str(item.get("sex4zhCN") or ""), gender):
            continue
        code, name, price = item.get("productCode"), item.get("name4zhCN"), item.get("minPrice")
        if not (isinstance(code, str) and isinstance(name, str) and isinstance(price, int | float)):
            continue
        offers.append(
            Candidate(
                source=OFFICIAL_CN_SOURCE,
                source_product_id=hashlib.sha1(code.encode()).hexdigest()[:16],
                title=f"Uniqlo {name} {item.get('code') or ''}".strip(),
                brand="Uniqlo",
                price=round(price),  # ponytail: whole yuan; 79.9 counts as 80
                currency=CNY,
                url=f"{UNIQLO_SITE}/product-detail.html?productCode={code}",
                image_url=f"{UNIQLO_SITE}{item['mainPic']}" if item.get("mainPic") else None,
                official_seller=True,
            )
        )
    return offers[:_PER_STORE]


async def _uniqlo(client: httpx.AsyncClient, word: str, gender: str | None) -> list[Candidate]:
    body = {
        "url": f"/search.html?description={word}&searchType=1",
        "pageInfo": {"page": 1, "pageSize": _PAGE_SIZE, "withSideBar": "N"},
        "belongTo": "pc",
        "rank": "overall",
        "priceRange": {"low": 0, "high": 0},
        "color": [],
        "size": [],
        "season": [],
        "material": [],
        "sex": [],
        "categoryFilter": {},
        "identity": [],
        "insiteDescription": "",
        "exist": [],
        "searchFlag": True,
        "description": word,
    }
    response = await client.post(
        UNIQLO_SEARCH, json=body, headers=_UNIQLO_HEADERS, timeout=_TIMEOUT_S
    )
    response.raise_for_status()
    return parse_uniqlo(response.json(), gender)


Reader = Callable[[httpx.AsyncClient, str, str | None], Awaitable[list[Candidate]]]
# Brand -> how the query names it, and the reader of its Chinese site.
_STORES: dict[str, tuple[re.Pattern[str], Reader]] = {
    "Uniqlo": (re.compile(r"uniqlo|юникло|优衣库", re.IGNORECASE), _uniqlo),
}


async def cn_store_candidates(
    constraints: QueryConstraints, client: httpx.AsyncClient | None = None
) -> list[Candidate]:
    """Offers of the Chinese sites of the brands the query names; ``[]`` when none applies."""
    word = ZH_TYPES.get(constraints.product_type)
    wanted = [
        (brand, read)
        for brand, (named, read) in _STORES.items()
        if named.search(constraints.search_phrase)
    ]
    if word is None or not wanted:
        return []
    own = client is None
    client = client or httpx.AsyncClient()
    offers: list[Candidate] = []
    try:
        for brand, read in wanted:
            try:
                offers += await read(client, word, constraints.gender)
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("%s China site unavailable: %r", brand, exc)
    finally:
        if own:
            await client.aclose()
    return offers
