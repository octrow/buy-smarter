"""Official brand stores as a price source (docs/review/2026-10-21-2354/img_10-review.md, item 6).

The cheapest genuine items are often on the brand's own site (puma.kz from 13 000 ₸)
rather than on marketplaces. Store catalog pages publish their products as
schema.org JSON-LD (``ItemList`` of ``Product`` with an ``Offer``), so one parser
reads any such store without per-site scraping. Used twice:

* ``learn-category`` checks each store URL the knowledge names and keeps the
  cheapest in-stock price as the brand's ``price_floors`` entry (a real page, not a guess);
* a search adds the stores' cheapest in-budget products as ``official`` candidates.

Best-effort throughout: a store that blocks us (intertop.kz answers 403) or renders
its catalog in JavaScript only yields nothing.
"""

import asyncio
import hashlib
import ipaddress
import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel

from buysmarter.knowledge.brands import BrandBook
from buysmarter.ranking.audience import other_audience, query_audience
from buysmarter.ranking.models import Candidate, QueryConstraints

logger = logging.getLogger(__name__)

SOURCE = "official"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}
_TIMEOUT_S = 20.0
_CONCURRENCY = 3
_TRUSTED_TIERS = ("A", "B")
# Cheapest in-budget products kept per store: a store page is one brand, a few suffice.
_PER_STORE = 3
_LD_JSON_RE = re.compile(
    r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)
_OUT_OF_STOCK = ("OutOfStock", "SoldOut", "Discontinued")
_MAX_BYTES = 8_000_000  # adidas.kz's catalog page is ~1.7 MB
_MAX_REDIRECTS = 3
# ponytail: per-process cache; the CLI runs once per process, so only the app and the
# worker benefit. Move it to the local store if CLI runs feel the latency.
_CACHE_TTL_S = 3600.0
_CACHE: dict[str, tuple[float, list["StoreOffer"]]] = {}


class StoreOffer(BaseModel):
    """One product from a store page's JSON-LD."""

    name: str
    url: str | None = None
    price: int
    in_stock: bool = True
    image_url: str | None = None  # the main photo (the style axis looks at it)


def parse_store_offers(html: str, page_url: str = "") -> list[StoreOffer]:
    """Every priced KZT product in the page's JSON-LD blocks (bad blocks are skipped).

    Product links are resolved against ``page_url``; anything but https is dropped.
    """
    offers: list[StoreOffer] = []
    for block in _LD_JSON_RE.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        _collect_products(data, offers)
    return [
        o.model_copy(
            update={
                "url": _safe_link(page_url, o.url),
                "image_url": _safe_link(page_url, o.image_url),
            }
        )
        for o in offers
    ]


def _safe_link(page_url: str, link: str | None) -> str | None:
    if not link:
        return None
    absolute = urljoin(page_url, link)
    return absolute if urlsplit(absolute).scheme == "https" else None


def _collect_products(node: Any, out: list[StoreOffer]) -> None:
    if isinstance(node, list):
        for item in node:
            _collect_products(item, out)
        return
    if not isinstance(node, dict):
        return
    if node.get("@type") == "Product":
        offer = _product_offer(node)
        if offer is not None:
            out.append(offer)
        return
    for value in node.values():
        _collect_products(value, out)


def _product_offer(product: dict[str, Any]) -> StoreOffer | None:
    raw = product.get("offers")
    offer = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(offer, dict) or offer.get("priceCurrency", "KZT") != "KZT":
        return None
    price = _int_price(offer.get("price") or offer.get("lowPrice"))
    name = product.get("name")
    if price is None or not isinstance(name, str):
        return None
    availability = str(offer.get("availability") or "")
    return StoreOffer(
        name=name,
        url=product.get("url") if isinstance(product.get("url"), str) else None,
        price=price,
        in_stock=not any(mark in availability for mark in _OUT_OF_STOCK),
        image_url=_first_image(product.get("image")),
    )


def _first_image(image: Any) -> str | None:
    """schema.org ``image``: a link, an ImageObject, or a list of either."""
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")
    return image if isinstance(image, str) else None


def _int_price(value: Any) -> int | None:
    try:
        price = int(float(str(value).replace(" ", "").replace(",", ".")))
    except (ValueError, OverflowError):
        return None
    return price if price > 0 else None


async def fetch_store_offers(client: httpx.AsyncClient, url: str) -> list[StoreOffer]:
    """In-stock products of one store page; empty on any failure. Cached per URL."""
    cached = _CACHE.get(url)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_TTL_S:
        return cached[1]
    try:
        html = await _get_page(client, url)
    except Exception as exc:  # noqa: BLE001 - one store must never break a search
        logger.warning("official store %s unavailable: %r", url, exc)
        return []
    offers = [o for o in parse_store_offers(html, url) if o.in_stock]
    _CACHE[url] = (time.monotonic(), offers)
    return offers


class StoreRefused(Exception):
    """A store page we will not read: not public https, too many redirects, too large."""


async def _get_page(client: httpx.AsyncClient, url: str) -> str:
    """The page body, following redirects by hand so every hop is checked.

    Store URLs come from LLM-generated knowledge: only public https hosts are fetched,
    and a body over ``_MAX_BYTES`` is abandoned.
    # ponytail: the host is resolved once for the check and again by httpx (DNS
    # rebinding window); pin the resolved IP in the transport if this ever runs exposed.
    """
    for _hop in range(_MAX_REDIRECTS + 1):
        if not await _is_public_https(url):
            raise StoreRefused(f"not a public https URL: {url}")
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            response.raise_for_status()
            return await _read_capped(response)
    raise StoreRefused(f"more than {_MAX_REDIRECTS} redirects from {url}")


async def _read_capped(response: httpx.Response) -> str:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > _MAX_BYTES:
            raise StoreRefused(f"{response.url} is over {_MAX_BYTES} bytes")
    return body.decode(response.encoding or "utf-8", errors="replace")


async def _is_public_https(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return False
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, parts.port or 443)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


def _store_targets(book: BrandBook) -> list[tuple[str, str]]:
    return [
        (brand, url)
        for brand, url in book.official_stores.items()
        if book.tiers.get(brand) in _TRUSTED_TIERS
    ]


async def _fetch_all(targets: list[tuple[str, str]]) -> list[tuple[str, str, list[StoreOffer]]]:
    semaphore = asyncio.Semaphore(_CONCURRENCY)
    async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT_S) as client:

        async def one(brand: str, url: str) -> tuple[str, str, list[StoreOffer]]:
            async with semaphore:
                return brand, url, await fetch_store_offers(client, url)

        return list(await asyncio.gather(*(one(b, u) for b, u in targets)))


async def verify_store_floors(book: BrandBook, category: str) -> BrandBook:
    """``book`` with each store's cheapest in-stock price as the brand's price floor.

    Only products for the category's audience count (a kids' pair must not set the
    men's floor). A store URL that yields no products is dropped, so only pages we
    could read stay.
    """
    targets = _store_targets(book)
    if not targets:
        return book
    gender = query_audience(category)
    floors: dict[str, int] = {}
    stores: dict[str, str] = {}
    for brand, url, offers in await _fetch_all(targets):
        fitting = _for_audience(offers, gender)
        if fitting:
            floors[brand] = min(o.price for o in fitting)
            stores[brand] = url
    unchecked = {b: u for b, u in book.official_stores.items() if b not in dict(targets)}
    return book.model_copy(
        update={
            "price_floors": {**book.price_floors, **floors},
            "official_stores": {**unchecked, **stores},
        }
    )


def _keep_offer(name: str) -> str | None:
    return None


async def official_candidates(
    book: BrandBook | None,
    constraints: QueryConstraints,
    reject: Callable[[str], str | None] = _keep_offer,
) -> list[Candidate]:
    """The cheapest in-budget products of every known official store.

    ``reject`` is the query's listing check (another audience, say), applied to each offer
    name before the per-store cut, so a kids' pair never takes a place in it.
    """
    if book is None:
        return []
    targets = _store_targets(book)
    if not targets:
        return []
    budget = constraints.budget_max
    candidates: list[Candidate] = []
    for brand, _url, offers in await _fetch_all(targets):
        in_budget = sorted(
            (o for o in offers if reject(o.name) is None and (budget is None or o.price <= budget)),
            key=lambda o: o.price,
        )
        candidates.extend(_to_candidate(o, brand, book) for o in in_budget[:_PER_STORE])
    return candidates


def _for_audience(offers: list[StoreOffer], gender: str | None) -> list[StoreOffer]:
    if gender is None:
        return offers
    return [o for o in offers if other_audience(gender, o.name) is None]


def _to_candidate(offer: StoreOffer, brand: str, book: BrandBook) -> Candidate:
    key = offer.url or f"{brand}:{offer.name}:{offer.price}"
    return Candidate(
        source=SOURCE,
        source_product_id=hashlib.sha1(key.encode()).hexdigest()[:16],
        title=f"{brand} {offer.name}",
        brand=brand,
        brand_tier=book.tiers.get(brand),
        price=offer.price,
        url=offer.url,
        image_url=offer.image_url,
        official_seller=True,
        reference_price=book.price_floors.get(brand),
        reference_kind="brand" if brand in book.price_floors else None,
    )
