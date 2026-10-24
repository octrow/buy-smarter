"""Shared GUI fixtures: a live CoreRunner and a fixture RecommendResult."""

import asyncio

import pytest

from buysmarter.gui import learning as gui_learning
from buysmarter.gui.bridge import CoreRunner
from buysmarter.ranking.models import (
    AxisScore,
    Candidate,
    QueryConstraints,
    Recommendation,
    ScoredCandidate,
)
from buysmarter.services import doctor
from buysmarter.services.doctor import Check
from buysmarter.services.matching import UsedListing
from buysmarter.services.recommend import RecommendResult

WAIT_MS = 3000


@pytest.fixture(autouse=True)
def _healthy_checks(monkeypatch):
    """Windows run the doctor checks at start; the real ones launch Playwright."""
    checks = [
        Check("Storage", True, "", "SQLite test.db"),
        Check(doctor.OZON_BROWSER, True, ""),
    ]
    monkeypatch.setattr(doctor, "doctor_checks", lambda: checks)


@pytest.fixture
def runner(qapp):
    r = CoreRunner()
    yield r
    r.shutdown()


def scored(source: str, pid: str, title: str, price: int, total: float, specs: float):
    cand = Candidate(
        source=source,
        source_product_id=pid,
        title=title,
        price=price,
        url=f"https://{source}.kz/p/{pid}",
    )
    axes = [
        AxisScore(name="price", score=20.0, max_score=25.0, explanation="ниже медианы рынка"),
        AxisScore(name="specs", score=specs, max_score=20.0, explanation="IPX7 есть"),
    ]
    return ScoredCandidate(
        candidate=cand, total_score=total, confidence=0.8, axes=axes, pros=["цена"], cons=[]
    )


@pytest.fixture
def result() -> RecommendResult:
    return make_result()


def make_result() -> RecommendResult:
    ranked = [
        scored("kaspi", "1", "Philips Sonicare Power Flosser", 38000, 88.0, 18.0),
        scored("ozon", "2", "Waterpik WP-660 Aquarius", 35000, 81.0, 12.0),
        scored("kaspi", "3", "Xiaomi Water Flosser 2", 19000, 74.0, 15.0),
    ]
    rec = Recommendation(
        best=ranked[0],
        alternatives={"best_budget": ranked[2]},
        ranked=ranked,
        rejected_count=3,
        rejected_reasons={"price exceeds budget": 2, "excluded brand": 1},
    )
    used = [
        UsedListing(
            title="ирригатор xiaomi б/у",
            price=9000,
            saving=10000,
            city="Алматы",
            url="https://olx.kz/d/1",
        )
    ]
    constraints = QueryConstraints(search_phrase="ирригатор", city="Алматы", budget_max=40000)
    return RecommendResult(recommendation=rec, constraints=constraints, used_listings=used)


class Fakes:
    """A stand-in for the learn; it waits on a gate per category."""

    def __init__(self) -> None:
        self.outcome = True
        self.learned: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}

    async def learn(self, category):
        self.learned.append(category)
        gate = self.gates.setdefault(category, asyncio.Event())
        await gate.wait()
        return self.outcome

    def release(self, category):
        gate = self.gates[category]
        gate._loop.call_soon_threadsafe(gate.set)  # the gate lives on the core loop


@pytest.fixture
def fakes(monkeypatch):
    f = Fakes()
    monkeypatch.setattr(gui_learning, "auto_learn", f.learn)
    return f


@pytest.fixture
def counted(monkeypatch):
    starts = []
    monkeypatch.setattr(gui_learning, "count_start", lambda: starts.append(1))
    return starts
