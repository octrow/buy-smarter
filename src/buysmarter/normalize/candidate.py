"""Pure normalization: raw source data → ``ranking.Candidate``.

No I/O. Combines a ``RawProduct`` search hit with its fetched ``specs`` and
``offers`` into the single shape the ranking engine consumes. Price is the
cheapest live offer, falling back to the search-card price; availability is
simply "do we have a usable price".
"""

from typing import NamedTuple

from market_sources.base import RawOffer, RawProduct

from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.ranking.models import Candidate

# Spec keys that name the brand; first hit wins.
_BRAND_KEYS = ("Бренд", "Brand")
# "Производитель" is often a country ("Китай") or a factory, not a brand: it counts
# only when the brand book knows it, never as evidence against the title's brand.
_MAKER_KEY = "Производитель"

_MIN_SELLER_REVIEWS = 20
_MAX_PRICE_PREMIUM = 1.10  # seller may cost up to 10% over the cheapest offer


def _pick_best_seller(offers: list[RawOffer]) -> str | None:
    """Display string for the best-rated trustworthy seller near the min price."""
    live = [o for o in offers if _is_live(o)]
    if not live:
        return None
    limit = min(o.price for o in live) * _MAX_PRICE_PREMIUM
    ok = [o for o in live if _is_trusted_seller(o) and o.price <= limit]
    if not ok:
        return None
    o = max(ok, key=lambda x: (x.merchant_rating, -x.price))
    price = f"{int(o.price):,}".replace(",", " ")
    return f"{o.seller} — {o.merchant_rating:.1f}★, {o.merchant_review_count} отзывов, {price} ₸"


def _is_live(offer: RawOffer) -> bool:
    """In stock at a positive price."""
    return bool(offer.in_stock and offer.price and offer.price > 0)


def _is_trusted_seller(offer: RawOffer) -> bool:
    """A named seller with a rating and enough reviews to trust it."""
    return bool(
        offer.seller
        and offer.merchant_rating is not None
        and (offer.merchant_review_count or 0) >= _MIN_SELLER_REVIEWS
    )


def _raw_brand(specs: dict[str, str], product: RawProduct, book: BrandBook | None) -> str | None:
    """The listing's own brand field: specs, else the search card's, else a known maker."""
    for key in _BRAND_KEYS:
        if specs.get(key):
            return specs[key]
    card_brand = getattr(product, "brand", None)
    if card_brand:
        return str(card_brand)
    maker = specs.get(_MAKER_KEY)
    if maker and (book is None or book.resolve(maker)[0] is not None):
        return maker
    return None


def _best_offer(offers: list[RawOffer]) -> RawOffer | None:
    """Cheapest offer with a positive price; its price is the candidate's price."""
    priced = [o for o in offers if o.price and o.price > 0]
    return min(priced, key=lambda o: o.price) if priced else None


def pick_price(product: RawProduct, offers: list[RawOffer]) -> float | None:
    """Cheapest positive offer price, else the search-card price."""
    best = _best_offer(offers)
    if best is not None:
        return best.price
    return product.price


# Brand-field values that mean "no brand" (Kaspi writes "Без бренда" for no-name goods).
_NO_BRAND_VALUES = frozenset(
    {"без бренда", "нет бренда", "no brand", "noname", "no name", "ноунейм"}
)


class _Brand(NamedTuple):
    name: str | None
    tier: str | None
    from_title: bool = False
    # A known brand the title names although the card's own brand field says otherwise.
    claimed: str | None = None


def _is_no_brand(raw_brand: str) -> bool:
    return raw_brand.strip().lower() in _NO_BRAND_VALUES


def _resolve_brand(raw_brand: str | None, title: str, book: BrandBook | None) -> _Brand:
    """Canonical brand + tier, trusting the card's brand field over the title.

    The title is a fallback only when the card has no brand field at all. A card
    whose brand field is unknown or "Без бренда" while its title names a known
    brand ("PODPODOSHVOI ... yeezy", "XAN Air Jordan 4") is how a counterfeit
    looks: it keeps no tier and the title's brand is returned as ``claimed``.
    # ponytail: a genuine sub-label in the brand field ("Jordan" for Nike) unknown
    # to the book reads as a claim too; teach the book that alias when it shows up.
    """
    explicit_none = raw_brand is not None and _is_no_brand(raw_brand)
    raw = None if explicit_none else raw_brand
    if book is None:
        return _Brand(raw, None)
    if raw:
        canonical, tier = book.resolve(raw)
        if canonical is not None:
            return _Brand(canonical, tier)
        claimed = book.title_brand(title)
        return _Brand(raw, None, claimed=claimed if claimed != raw else None)
    named = book.title_brand(title)
    if explicit_none:
        return _Brand(None, None, claimed=named)
    if named is None:
        return _Brand(None, None)
    return _Brand(named, book.tiers.get(named), from_title=True)


def _mimic_brand(
    book: BrandBook | None, title: str, raw_brand: str | None, brand: _Brand
) -> str | None:
    """A brand the listing pretends to be: claimed in the title against the card's
    brand field, or spelled to dodge the brand check ("J BL"), unless it is its own."""
    if book is None:
        return None
    if brand.claimed is not None:
        return brand.claimed
    mimic = book.mimicry(title) or book.mimicry(raw_brand or "")
    return mimic if mimic is not None and mimic != brand.name else None


def _is_official_seller(
    product: RawProduct, offers: list[RawOffer], brand: str | None, book: BrandBook | None
) -> bool:
    """Sold by the brand's own store / official partner (marketplace flag or seller name)."""
    if getattr(product, "official_partner", None):
        return True
    if book is None:
        return False
    return any(book.is_official_seller(brand, o.seller) for o in offers if _is_live(o))


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
    price = pick_price(product, offers)
    raw_brand = _raw_brand(specs, product, book)
    brand = _resolve_brand(raw_brand, product.title, book)
    has_price = bool(price and price > 0)
    best = _best_offer(offers)
    return Candidate(
        source=product.source,
        source_product_id=product.product_id,
        title=product.title,
        brand=brand.name,
        brand_tier=brand.tier,
        brand_from_title=brand.from_title,
        price=int(price) if price and has_price else 0,
        rating=product.rating,
        review_count=product.review_count,
        specs=specs,
        availability=has_price,
        url=product.url,
        image_url=product.image_url,
        offer_count=len(offers),
        best_seller=_pick_best_seller(offers),
        mimic_brand=_mimic_brand(book, product.title, raw_brand, brand),
        official_seller=_is_official_seller(product, offers, brand.name, book),
        category_path=list(getattr(product, "category_path", None) or []),
        delivery_date=best.delivery_date if best else None,
        delivery_cost=best.delivery_cost if best else None,
    )


def attach_reference_prices(
    candidates: list[Candidate], book: BrandBook | None, model_book: ModelBook | None
) -> list[Candidate]:
    """Copies of ``candidates`` carrying the genuine-item price they are judged against.

    The model's official price when the title resolves to a known model of the
    candidate's own brand, else the brand's cheapest genuine item (``price_floors``).
    Untouched when there is no knowledge for the candidate.
    """
    return [_with_reference(c, book, model_book) for c in candidates]


def _with_reference(
    candidate: Candidate, book: BrandBook | None, model_book: ModelBook | None
) -> Candidate:
    if candidate.brand_tier is None or candidate.brand is None:
        return candidate
    entry = model_book.resolve_entry(candidate.title) if model_book else None
    if entry is not None and entry.reference_price and entry.brand == candidate.brand:
        return candidate.model_copy(
            update={"reference_price": entry.reference_price, "reference_kind": "model"}
        )
    floor = book.price_floors.get(candidate.brand) if book else None
    if floor:
        return candidate.model_copy(update={"reference_price": floor, "reference_kind": "brand"})
    return candidate
