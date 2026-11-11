"""Landed cost of an offer: what reaching the door costs, part by part (CONTEXT.md "Landed cost").

Pure: the rates (KZT per unit of a currency, the National Bank's) and the user's own tariffs
come in. Every route an offer has is priced; the cheapest is the answer, the next one its
alternative.
"""

from collections.abc import Mapping

from pydantic import BaseModel

from buysmarter.knowledge.landed_cost_rules import (
    BANK_MARGIN,
    CARGO_USD_PER_KG,
    DEFAULT_WEIGHT_KG,
    DIRECT_VAT,
    DUTY_EUR_PER_KG,
    DUTY_FREE_EUR,
    DUTY_FREE_KG,
    DUTY_RATE,
    MARKETPLACE,
    SOURCE_ROUTES,
    TYPICAL_WEIGHT_KG,
    VOLUMETRIC_DIVISOR,
    Route,
)
from buysmarter.ranking.models import Candidate, ScoredCandidate

KZT = "KZT"


class Part(BaseModel, frozen=True):
    """One part of a landed cost, in tenge."""

    label: str
    kzt: int


class Tariffs(BaseModel, frozen=True):
    """The user's own costs (settings): the bank's mark-up and their cargo's rate."""

    bank_margin: float = BANK_MARGIN
    cargo_usd_per_kg: float = CARGO_USD_PER_KG.value


DEFAULT_TARIFFS = Tariffs()


class LandedCost(BaseModel, frozen=True):
    """An offer's cost to the door by one route; ``alternative`` is the next cheapest route."""

    route: Route
    total: int
    parts: tuple[Part, ...]
    weight_estimated: bool = False
    alternative: "LandedCost | None" = None


def duty_eur(value_eur: float, kg: float) -> float:
    """Duty on one parcel: by value over the threshold or by weight over it, the larger."""
    by_value = max(0.0, value_eur - DUTY_FREE_EUR.value) * DUTY_RATE.value
    by_weight = max(0.0, kg - DUTY_FREE_KG.value) * DUTY_EUR_PER_KG.value
    return max(by_value, by_weight)


def billable_kg(actual_kg: float, dims_cm: tuple[float, float, float] | None) -> float:
    """The weight a carrier bills: the actual one, or the volumetric one when larger."""
    if dims_cm is None:
        return actual_kg
    length, width, height = dims_cm
    return max(actual_kg, length * width * height / VOLUMETRIC_DIVISOR.value)


def _price_parts(candidate: Candidate, rates: Mapping[str, float], tariffs: Tariffs) -> list[Part]:
    """The price in tenge, plus the bank's mark-up when paid in another currency."""
    if candidate.currency == KZT:
        return [Part(label="цена", kzt=candidate.price)]
    price = round(candidate.price * rates[candidate.currency])
    return [
        Part(label="цена", kzt=price),
        Part(label="конвертация", kzt=round(price * tariffs.bank_margin)),
    ]


def _route_cost(
    candidate: Candidate,
    route: Route,
    product_type: str,
    rates: Mapping[str, float],
    tariffs: Tariffs,
) -> LandedCost:
    parts = _price_parts(candidate, rates, tariffs)
    goods = sum(p.kzt for p in parts)
    estimated = False
    if route.key in {"direct", "cargo"}:
        kg = candidate.weight_kg
        if kg is None:
            kg, estimated = TYPICAL_WEIGHT_KG.get(product_type, DEFAULT_WEIGHT_KG), True
        if route.key == "direct":
            parts.append(Part(label="НДС", kzt=round(goods * DIRECT_VAT.value)))
        else:
            cargo = kg * tariffs.cargo_usd_per_kg * rates["USD"]
            parts.append(Part(label="карго", kzt=round(cargo)))
        if duty := duty_eur(goods / rates["EUR"], kg):
            parts.append(Part(label="пошлина", kzt=round(duty * rates["EUR"])))
    elif route == MARKETPLACE:
        parts.append(Part(label="доставка", kzt=round(candidate.delivery_cost or 0)))
    total = sum(p.kzt for p in parts)
    return LandedCost(route=route, total=total, parts=tuple(parts), weight_estimated=estimated)


def landed_cost(
    candidate: Candidate,
    product_type: str,
    rates: Mapping[str, float],
    routes: tuple[Route, ...] | None = None,
    tariffs: Tariffs = DEFAULT_TARIFFS,
) -> LandedCost:
    """The offer's cheapest landed cost, with the next cheapest route as its alternative."""
    options = routes or SOURCE_ROUTES.get(candidate.source, (MARKETPLACE,))
    costs = sorted(
        (_route_cost(candidate, r, product_type, rates, tariffs) for r in options),
        key=lambda c: c.total,
    )
    best = costs[0]
    return best.model_copy(update={"alternative": costs[1]}) if len(costs) > 1 else best


class MarketRow(BaseModel, frozen=True):
    """One marketplace in the comparison: its best-ranked offer and that offer's landed cost."""

    offer: ScoredCandidate
    cost: LandedCost


def market_rows(
    ranked: list[ScoredCandidate],
    product_type: str,
    rates: Mapping[str, float],
    tariffs: Tariffs = DEFAULT_TARIFFS,
) -> list[MarketRow]:
    """The best-ranked offer of every marketplace, cheapest to the door first.

    An offer priced in a currency ``rates`` lacks is left out, not guessed.
    """
    firsts: dict[str, ScoredCandidate] = {}
    for scored in ranked:
        currency = scored.candidate.currency
        if scored.candidate.price > 0 and (currency == KZT or currency in rates):
            firsts.setdefault(scored.candidate.source, scored)
    rows = [
        MarketRow(offer=s, cost=landed_cost(s.candidate, product_type, rates, tariffs=tariffs))
        for s in firsts.values()
    ]
    return sorted(rows, key=lambda row: row.cost.total)
