"""Tests for wiring the category-relevance filter into the recommend pipeline."""

import asyncio
from types import SimpleNamespace

from buysmarter.llm import relevance as rel
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
    async def _fake_filter(category, titles, keep_threshold=0.25, **_):
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

    async def _fake_filter(category, titles, keep_threshold=0.25, **_):
        nonlocal called
        called = True
        return [True] * len(titles)

    monkeypatch.setattr(svc, "filter_relevant", _fake_filter)

    pool = [_cand("1", "Робот-пылесос A"), _cand("2", "Робот-пылесос B")]
    await svc._filter_relevant_candidates(pool, "робот пылесос", use_llm=True)
    assert called is False


async def test_never_drops_below_three(monkeypatch) -> None:
    async def _drop_all(category, titles, keep_threshold=0.25, **_):
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


async def test_a_title_restored_to_meet_the_floor_is_logged(caplog) -> None:
    pool = [_cand("1", "Аккумулятор для Dyson V8", rating=4.9), _cand("2", "Насадка для Dyson")]

    with caplog.at_level("INFO", logger=svc.__name__):
        kept = await svc._filter_relevant_candidates(
            pool, "робот пылесос", use_llm=False, min_keep=1
        )

    assert [c.title for c in kept] == ["Аккумулятор для Dyson V8"]
    assert "relevance: restore 'Аккумулятор для Dyson V8' (to meet the floor)" in caplog.messages


async def test_a_title_naming_the_queried_model_skips_the_llm_pass(monkeypatch) -> None:
    async def _drop_all(category, titles, keep_threshold=0.25, **_):
        return [False] * len(titles)

    monkeypatch.setattr(svc, "filter_relevant", _drop_all)

    pool = [
        _cand("1", "iPhone 13 128gb 75%/рассрочка/baribarshop"),
        _cand("2", "Iphone 13 128 gb midnight"),
        _cand("3", "Смартфон iPhone 13"),
        _cand("4", "iPhone 11 128GB Black"),
        _cand("5", "iPhone 12 128GB Black"),
        _cand("6", "Смартфон Vivo y12"),
    ]
    kept = await svc._filter_relevant_candidates(
        pool, "смартфон iPhone 13", use_llm=True, min_keep=1
    )
    assert [c.source_product_id for c in kept] == ["1", "2", "3"]


async def test_used_filter_keeps_only_model_titles_when_every_classifier_fails(monkeypatch) -> None:
    # No floor for OLX ads: an empty section beats a wrong one (#16).
    async def _down(prompt, response_model, *, model=None, **kwargs):
        raise rel.LlmError("cli down")

    monkeypatch.setattr(rel, "generate", _down)
    ads = [_cand("1", "iPhone 13 128 GB midnight"), _cand("2", "Смартфон Samsung Galaxy A15")]

    kept = await svc._filter_relevant_candidates(
        ads, "смартфон iPhone 13", use_llm=True, min_keep=0
    )

    assert [c.title for c in kept] == ["iPhone 13 128 GB midnight"]


async def test_protected_candidates_count_towards_the_floor() -> None:
    # Five substance-confirmed gels already meet the floor: no accessory comes back (#17).
    gels = [_cand(str(i), f"Гель Клензит {i}") for i in range(5)]
    accessories = [_cand("a", "Аккумулятор для Dyson V8"), _cand("b", "Аккумулятор для Dyson V10")]

    kept = await svc._filter_relevant_candidates(
        gels + accessories,
        "пылесос",
        use_llm=False,
        protected_ids={c.source_product_id for c in gels},
    )

    assert kept == gels


async def test_new_and_used_relevance_passes_run_at_the_same_time(monkeypatch) -> None:
    both_started = asyncio.Barrier(2)

    async def _waits_for_the_other(candidates, *args, **kwargs):
        await asyncio.wait_for(both_started.wait(), timeout=1)
        return candidates

    async def _nothing(*args, **kwargs):
        return None

    async def _no_stores(*args, **kwargs):
        return []

    async def _sources(*args, **kwargs):
        return {"olx": [_cand("o1", "Пылесос б/у")]}

    monkeypatch.setattr(svc, "gather_sources", _sources)
    monkeypatch.setattr(svc, "official_candidates", _no_stores)
    monkeypatch.setattr(svc, "attach_price_history", _nothing)
    monkeypatch.setattr(svc, "_filter_relevant_candidates", _waits_for_the_other)
    monkeypatch.setattr(svc, "_filter_used", _waits_for_the_other)
    capabilities = SimpleNamespace(
        searches=lambda: [], reject_listing=lambda text: None, protected=lambda cands: set()
    )
    run = SimpleNamespace(
        constraints=SimpleNamespace(search_phrase="пылесос", city="almaty", budget_max=None),
        profile=SimpleNamespace(brand_book=None, model_book=None),
        capabilities=capabilities,
        use_llm=True,
    )

    pool = await svc._collect(run, {})

    assert [c.source_product_id for c in pool.used] == ["o1"]
