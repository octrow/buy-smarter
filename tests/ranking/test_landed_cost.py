"""Landed cost: route choice, the 200 EUR / 31 kg duty threshold, volumetric weight."""

from datetime import date

from buysmarter.knowledge.landed_cost_rules import CARGO, DIRECT, MARKETPLACE, stale_rules
from buysmarter.ranking.landed_cost import billable_kg, duty_eur, landed_cost
from buysmarter.ranking.models import Candidate

# KZT per unit, the National Bank's way.
_RATES = {"EUR": 600.0, "USD": 500.0, "CNY": 70.0}


def _offer(**overrides) -> Candidate:
    fields = dict(source="kaspi", source_product_id="1", title="Худи", price=20_000)
    fields.update(overrides)
    return Candidate(**fields)


def test_a_marketplace_offer_costs_its_price_plus_the_cards_delivery():
    cost = landed_cost(_offer(delivery_cost=1_500), "худи", _RATES)

    assert cost.route == MARKETPLACE
    assert cost.total == 21_500
    assert [p.label for p in cost.parts] == ["цена", "доставка"]


def test_free_or_unknown_marketplace_delivery_counts_as_zero():
    assert landed_cost(_offer(), "худи", _RATES).total == 20_000


def test_no_duty_up_to_200_eur_and_31_kg():
    assert duty_eur(200, 31) == 0


def test_duty_is_15_percent_over_200_eur_or_2_eur_per_kg_over_31_whichever_larger():
    assert duty_eur(350, 5) == 22.5
    assert duty_eur(120, 40) == 18
    assert duty_eur(300, 50) == 38  # 15 by value, 38 by weight


def test_volumetric_weight_counts_when_the_parcel_is_bulky():
    assert billable_kg(1.0, (60, 40, 25)) == 10.0
    assert billable_kg(2.0, (10, 10, 10)) == 2.0
    assert billable_kg(2.0, None) == 2.0


def test_a_foreign_offer_takes_the_cheaper_route_and_keeps_the_other():
    light = _offer(source="taobao", price=300, currency="CNY", weight_kg=0.7)

    cost = landed_cost(light, "худи", _RATES, routes=(DIRECT, CARGO))

    # 300 CNY = 21 000 ₸ + 2 % bank = 21 420; cargo 0.7 kg × $3.8 × 500 = 1 330
    assert cost.route == CARGO
    assert cost.total == 22_750
    assert cost.alternative is not None and cost.alternative.route == DIRECT
    assert cost.alternative.total == 21_420 + round(21_420 * 0.16)


def test_without_a_weight_the_typical_one_is_used_and_flagged():
    cost = landed_cost(_offer(price=300, currency="CNY"), "пуховик", _RATES, routes=(CARGO,))

    assert cost.weight_estimated
    assert ("карго", round(3.0 * 3.8 * 500)) in [(p.label, p.kzt) for p in cost.parts]


def test_a_foreign_offer_over_200_eur_pays_duty():
    pricey = _offer(price=2_000, currency="CNY", weight_kg=1.0)  # 140 000 ₸ = 233 €

    cost = landed_cost(pricey, "куртка", _RATES, routes=(CARGO,))

    duty = next(p.kzt for p in cost.parts if p.label == "пошлина")
    assert duty == round((142_800 / 600 - 200) * 0.15 * 600)


def test_rules_turn_stale_after_90_days():
    assert stale_rules(date(2026, 12, 1)) == []
    assert len(stale_rules(date(2027, 3, 1))) > 0


def _scored(source: str, pid: str, price: int, score: float, **overrides):
    from buysmarter.ranking.models import ScoredCandidate

    offer = _offer(source=source, source_product_id=pid, price=price, **overrides)
    return ScoredCandidate(candidate=offer, total_score=score, confidence=0.8, axes=[])


def test_the_comparison_takes_each_marketplaces_best_offer_cheapest_first():
    from buysmarter.ranking.landed_cost import market_rows

    ranked = [
        _scored("kaspi", "k1", 25_000, 90),
        _scored("ozon", "o1", 19_000, 80, delivery_cost=2_000),
        _scored("kaspi", "k2", 10_000, 70),
        _scored("taobao", "t1", 100, 60, currency="CNY"),  # no CNY rate: left out
    ]

    rows = market_rows(ranked, "худи", {})

    assert [(r.offer.candidate.source_product_id, r.cost.total) for r in rows] == [
        ("o1", 21_000),
        ("k1", 25_000),
    ]
