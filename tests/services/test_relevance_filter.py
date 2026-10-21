"""Tests for wiring the category-relevance filter into the recommend pipeline."""

from buysmarter.ranking.models import Candidate
from buysmarter.services import recommend as svc


def _cand(pid: str, title: str, price: int = 20000, rating: float | None = None) -> Candidate:
    return Candidate(source="kaspi", source_product_id=pid, title=title, price=price, rating=rating)


async def test_heuristic_runs_without_llm() -> None:
    pool = [
        _cand("1", "Робот-пылесос Roborock Q7"),
        _cand("2", "Робот-пылесос Xiaomi S10"),
        _cand("3", "Робот-пылесос Dreame D9"),
        _cand("4", "Аккумулятор для Dyson V8"),  # accessory → dropped
    ]
    kept = await svc._filter_relevant_candidates(pool, "робот пылесос", use_llm=False)
    titles = [c.title for c in kept]
    assert "Аккумулятор для Dyson V8" not in titles
    assert len(kept) == 3


async def test_llm_pass_drops_flagged_candidates(monkeypatch) -> None:
    async def _fake_filter(category, titles, keep_threshold=0.25):
        # Drop the second surviving candidate.
        return [t != "Батарея-пылесос noname" for t in titles]

    monkeypatch.setattr(svc, "filter_relevant", _fake_filter)

    pool = [
        _cand("1", "Робот-пылесос Roborock Q7"),
        _cand("2", "Батарея-пылесос noname"),  # heuristic keeps (has head noun), LLM drops
        _cand("3", "Робот-пылесос Xiaomi S10"),
        _cand("4", "Робот-пылесос Dreame D9"),
    ]
    kept = await svc._filter_relevant_candidates(pool, "робот пылесос", use_llm=True)
    assert "Батарея-пылесос noname" not in [c.title for c in kept]
    assert len(kept) == 3


async def test_llm_pass_skipped_when_pool_small(monkeypatch) -> None:
    called = False

    async def _fake_filter(category, titles, keep_threshold=0.25):
        nonlocal called
        called = True
        return [True] * len(titles)

    monkeypatch.setattr(svc, "filter_relevant", _fake_filter)

    pool = [_cand("1", "Робот-пылесос A"), _cand("2", "Робот-пылесос B")]
    await svc._filter_relevant_candidates(pool, "робот пылесос", use_llm=True)
    assert called is False


async def test_never_drops_below_three(monkeypatch) -> None:
    async def _drop_all(category, titles, keep_threshold=0.25):
        return [False] * len(titles)

    monkeypatch.setattr(svc, "filter_relevant", _drop_all)

    pool = [
        _cand("1", "Робот-пылесос A", rating=4.9),
        _cand("2", "Робот-пылесос B", rating=4.2),
        _cand("3", "Робот-пылесос C", rating=3.1),
        _cand("4", "Робот-пылесос D", rating=4.7),
    ]
    kept = await svc._filter_relevant_candidates(pool, "робот пылесос", use_llm=True)
    # LLM dropped everything → floor guard restores the 3 highest-rated.
    assert len(kept) == 3
    restored_ratings = sorted((c.rating for c in kept), reverse=True)
    assert restored_ratings == [4.9, 4.7, 4.2]
