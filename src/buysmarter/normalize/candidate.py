"""Pure normalization: raw source data → ``ranking.Candidate``.

No I/O. Combines a ``RawProduct`` search hit with its fetched ``specs`` and
``offers`` into the single shape the ranking engine consumes. Price is the
cheapest live offer, falling back to the search-card price; availability is
simply "do we have a usable price".
"""

from market_sources.base import RawOffer, RawProduct

from buysmarter.knowledge.brands import BrandBook
from buysmarter.ranking.models import Candidate

# Spec keys Kaspi uses for the brand; first hit wins.
_BRAND_KEYS = ("Бренд", "Производитель", "Brand")

_MIN_SELLER_REVIEWS = 20
_MAX_PRICE_PREMIUM = 1.10  # seller may cost up to 10% over the cheapest offer


def _pick_best_seller(offers: list[RawOffer]) -> str | None:
    """Display string for the best-rated trustworthy seller near the min price."""
    live = [o for o in offers if o.in_stock and o.price and o.price > 0]
    if not live:
        return None
    limit = min(o.price for o in live) * _MAX_PRICE_PREMIUM
    ok = [
        o
        for o in live
        if o.seller
        and o.merchant_rating is not None
        and (o.merchant_review_count or 0) >= _MIN_SELLER_REVIEWS
        and o.price <= limit
    ]
    if not ok:
        return None
    o = max(ok, key=lambda x: (x.merchant_rating, -x.price))
    price = f"{int(o.price):,}".replace(",", " ")
    return f"{o.seller} — {o.merchant_rating:.1f}★, {o.merchant_review_count} отзывов, {price} ₸"


def _brand_from_specs(specs: dict[str, str]) -> str | None:
    for key in _BRAND_KEYS:
        if specs.get(key):
            return specs[key]
    return None


def _best_offer(offers: list[RawOffer]) -> RawOffer | None:
    """Cheapest offer with a positive price; its price is the candidate's price."""
    priced = [o for o in offers if o.price and o.price > 0]
    return min(priced, key=lambda o: o.price) if priced else None


def _pick_price(product: RawProduct, offers: list[RawOffer]) -> float | None:
    """Cheapest positive offer price, else the search-card price."""
    best = _best_offer(offers)
    if best is not None:
        return best.price
    return product.price


def _resolve_brand(
    raw_brand: str | None, title: str, book: BrandBook | None
) -> tuple[str | None, str | None]:
    """Resolve canonical brand + trust tier from the brand field, then title."""
    if book is None:
        return raw_brand, None
    canonical, tier = book.resolve(raw_brand or "")
    if canonical is None:
        canonical, tier = book.resolve(title)
    return canonical or raw_brand, tier


def to_candidate(
    product: RawProduct,
    specs: dict[str, str] | None = None,
    offers: list[RawOffer] | None = None,
    book: BrandBook | None = None,
) -> Candidate:
    """Merge a ``RawProduct`` + ``specs`` + ``offers`` into a ``Candidate``.

    When a ``BrandBook`` is supplied, the brand is resolved to its canonical
    name and trust tier (checking the brand spec first, then the title).
    Brand-mimicry is checked unconditionally (not just when resolution fails):
    a counterfeit can genuinely resolve to one brand ("compatible with Xiaomi")
    while its title separately mimics another ("J BL Tws300") — ``mimicry()``
    already excludes the resolved brand itself, so ``mimic_brand`` is only set
    when the mimicked name differs from the resolved/fallback brand.
    """
    specs = specs or {}
    offers = offers or []
    price = _pick_price(product, offers)
    raw_brand = _brand_from_specs(specs)
    brand, brand_tier = _resolve_brand(raw_brand, product.title, book)
    mimic_brand = None
    if book is not None:
        mimic = book.mimicry(product.title) or book.mimicry(raw_brand or "")
        if mimic is not None and mimic != brand:
            mimic_brand = mimic
    best = _best_offer(offers)
    return Candidate(
        source=product.source,
        source_product_id=product.product_id,
        title=product.title,
        brand=brand,
        brand_tier=brand_tier,
        price=int(price) if price and price > 0 else 0,
        rating=product.rating,
        review_count=product.review_count,
        specs=specs,
        availability=bool(price and price > 0),
        url=product.url,
        offer_count=len(offers),
        best_seller=_pick_best_seller(offers),
        mimic_brand=mimic_brand,
        delivery_date=best.delivery_date if best else None,
        delivery_cost=best.delivery_cost if best else None,
    )
