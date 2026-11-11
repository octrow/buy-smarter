"""Landed-cost rules: every number with the day it was checked and where (CONTEXT.md "Landed cost").

Taxes and tariffs change; a rule older than ``STALE_AFTER_DAYS`` makes the report say so.
Delivery routes (CONTEXT.md "Delivery route") carry their time and effort; which routes a
source offers is ``SOURCE_ROUTES``.
"""

from datetime import date, timedelta

from pydantic import BaseModel

STALE_AFTER_DAYS = 90
_CHECKED = date(2026, 11, 11)
_CUSTOMS = "https://calk.kz/calculator/parcel-customs/"
_VAT = "https://1club.kz/blog/nds-temu-taobao-1-iyulya-kazahstan/"
_USER_NOTES = "заметки пользователя (t.me/podeshevlekz)"


class Rule(BaseModel, frozen=True):
    """One number of the rules, with its source and the day it was checked."""

    name: str
    value: float
    source: str
    checked: date


# Parcels from outside the EAEU: duty-free up to 200 EUR and 31 kg; above either, 15 % of
# the value over 200 EUR or 2 EUR per kg over 31 kg, whichever is larger (VAT included).
# The EAEU e-commerce regime (5 % of the full value + 16 % VAT) is postponed to 2027-01-01.
DUTY_FREE_EUR = Rule(name="порог пошлины, €", value=200, source=_CUSTOMS, checked=_CHECKED)
DUTY_FREE_KG = Rule(name="порог пошлины, кг", value=31, source=_CUSTOMS, checked=_CHECKED)
DUTY_RATE = Rule(name="пошлина сверх порога", value=0.15, source=_CUSTOMS, checked=_CHECKED)
DUTY_EUR_PER_KG = Rule(name="пошлина за кг сверх", value=2, source=_CUSTOMS, checked=_CHECKED)
# ponytail: added on direct shipping as the user's notes say; platforms may already put it
# into the price (the source above) — check against real prices when Taobao/PDD arrive.
DIRECT_VAT = Rule(name="НДС прямой доставки", value=0.16, source=_VAT, checked=_CHECKED)
CARGO_USD_PER_KG = Rule(name="карго, $/кг", value=3.8, source=_USER_NOTES, checked=_CHECKED)
# Billable weight of a bulky parcel: length × width × height (cm) / divisor.
VOLUMETRIC_DIVISOR = Rule(
    name="объёмный делитель", value=6000, source="стандарт курьерских служб", checked=_CHECKED
)
RULES = (
    DUTY_FREE_EUR,
    DUTY_FREE_KG,
    DUTY_RATE,
    DUTY_EUR_PER_KG,
    DIRECT_VAT,
    CARGO_USD_PER_KG,
    VOLUMETRIC_DIVISOR,
)

# The bank's mark-up over the National Bank rate when paying in another currency.
BANK_MARGIN = 0.02

# A parcel's billable weight by product type when the page gives none ("вес оценён"); a down
# jacket is counted by volume. ponytail: one rough table; refine from real parcels.
TYPICAL_WEIGHT_KG = {
    "футболка": 0.25,
    "рубашка": 0.35,
    "курта": 0.45,
    "худи": 0.7,
    "свитшот": 0.6,
    "джинсы": 0.8,
    "брюки": 0.6,
    "кроссовки": 1.3,
    "обувь": 1.3,
    "куртка": 1.5,
    "пуховик": 3.0,
}
DEFAULT_WEIGHT_KG = 1.0


class Route(BaseModel, frozen=True):
    """A delivery route: its name, how many days it takes and how much effort (1 easiest)."""

    key: str
    label: str
    days: tuple[int, int]
    effort: int


MARKETPLACE = Route(key="marketplace", label="курьер или ПВЗ", days=(1, 5), effort=1)
PRIVATE = Route(key="private", label="по договорённости с продавцом", days=(0, 3), effort=2)
DIRECT = Route(key="direct", label="прямая международная", days=(10, 25), effort=2)
CARGO = Route(key="cargo", label="карго", days=(14, 21), effort=3)

# Which routes each source offers; a source not named here delivers like a marketplace.
# A brand's Chinese site ships inside China only: a cargo forwarder brings the parcel over.
SOURCE_ROUTES: dict[str, tuple[Route, ...]] = {"olx": (PRIVATE,), "official_cn": (CARGO,)}


def stale_rules(today: date) -> list[Rule]:
    """The rules checked longer ago than ``STALE_AFTER_DAYS``."""
    limit = timedelta(days=STALE_AFTER_DAYS)
    return [rule for rule in RULES if today - rule.checked > limit]
