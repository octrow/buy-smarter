"""The comparison in ``recommend``: rates only for a foreign offer, the user's tariffs applied."""

import asyncio
from datetime import date
from types import SimpleNamespace

from buysmarter.capabilities.cross_market import CrossMarket
from buysmarter.ranking.models import Candidate, Recommendation, ScoredCandidate
from buysmarter.services import fx
from buysmarter.services import recommend as rec


def _scored(source: str, price: int, currency: str = "KZT") -> ScoredCandidate:
    offer = Candidate(
        source=source, source_product_id=source, title="Худи", price=price, currency=currency
    )
    return ScoredCandidate(candidate=offer, total_score=1, confidence=0.8, axes=[])


def _ranking(*offers: ScoredCandidate) -> Recommendation:
    return Recommendation(best=offers[0], ranked=list(offers))


def _run(on: bool = True) -> SimpleNamespace:
    items = [(CrossMarket(), None)] if on else []
    return SimpleNamespace(
        capabilities=SimpleNamespace(items=items),
        constraints=SimpleNamespace(product_type="худи"),
    )


def _wire(monkeypatch, stored: dict, fetched: list) -> None:
    async def rates(today):
        fetched.append(today)
        return fx.Rates(day=date(2026, 11, 11), kzt={"CNY": 70.0, "USD": 500.0, "EUR": 600.0})

    async def preference(key):
        return stored.get(key)

    monkeypatch.setattr(fx, "current_rates", rates)
    monkeypatch.setattr(rec, "_preference", preference)


def test_tenge_offers_need_no_rates(monkeypatch):
    fetched: list = []
    _wire(monkeypatch, {}, fetched)
    ranked = _ranking(_scored("kaspi", 20_000))

    rows, day = asyncio.run(rec._markets(_run(), ranked))

    assert fetched == [] and day is None
    assert [row.cost.total for row in rows] == [20_000]


def test_a_foreign_offer_is_converted_with_the_users_bank_margin(monkeypatch):
    _wire(monkeypatch, {"bank_margin": 5}, [])
    ranked = _ranking(_scored("kaspi", 20_000), _scored("official_cn", 100, "CNY"))

    rows, day = asyncio.run(rec._markets(_run(), ranked))

    assert day == date(2026, 11, 11)
    cny = next(row for row in rows if row.offer.candidate.currency == "CNY")
    assert [(p.label, p.kzt) for p in cny.cost.parts][:2] == [("цена", 7_000), ("конвертация", 350)]


def test_the_comparison_off_gives_nothing(monkeypatch):
    _wire(monkeypatch, {}, [])

    assert asyncio.run(rec._markets(_run(on=False), _ranking(_scored("kaspi", 1)))) == ([], None)
