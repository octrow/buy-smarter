"""run_job: replays a stored request through the pipeline and records one terminal outcome."""

import asyncio
from types import SimpleNamespace

import pytest
from fakeredis import FakeAsyncRedis

from buysmarter import worker
from buysmarter.jobs import JobStatus, JobStore, RecommendRequest
from buysmarter.services import auto_learn as auto_learn_mod
from buysmarter.services.matching import UsedListing
from buysmarter.services.recommend import NoNewPick, NoSubstanceMatch
from buysmarter.services.understanding import Clarification, NeedsClarification, NothingToSearch


@pytest.fixture
def store() -> JobStore:
    return JobStore(FakeAsyncRedis(decode_responses=True))


async def _queued(store: JobStore, **request) -> str:
    job = await store.create(
        RecommendRequest(query="ирригатор", sources=["kaspi"], **request), dedupe="d"
    )
    return job.id


def _pipeline(monkeypatch, behaviour) -> list[dict]:
    calls: list[dict] = []

    async def fake_recommend(query, **kwargs):
        calls.append({"query": query, **kwargs})
        return await behaviour()

    monkeypatch.setattr(worker, "recommend", fake_recommend)
    return calls


async def test_done_job_carries_the_report_and_the_best_offer(
    store, monkeypatch, recommend_result
) -> None:
    async def ok():
        return recommend_result

    calls = _pipeline(monkeypatch, ok)
    job_id = await _queued(store, budget=40000, top=3)

    await worker.run_job(store, job_id)
    job = await store.get(job_id)

    assert job.status is JobStatus.DONE
    assert job.result["report_markdown"].startswith("# buy-smarter: рекомендация")
    assert job.result["best"]["title"] == "Waterpik WP-100 ирригатор"
    assert job.result["best"]["axes"][0]["name"] == "brand"
    assert calls == [
        {
            "query": "ирригатор",
            "city": None,
            "budget": 40000,
            "use_llm": False,
            "trusted_only": False,
            "sources": ["kaspi"],
            "answer": None,
            "clarify_round": 0,
            "no_clarify": False,
        }
    ]


async def test_a_question_is_a_no_match_with_the_clarification(store, monkeypatch) -> None:
    """No new status: clients that wait for done/no_match/failed keep working."""
    asked = Clarification(
        kind="open", question="Что именно ищем?", options=["духи", "плед"], round=1
    )

    async def ask():
        raise NeedsClarification(asked)

    calls = _pipeline(monkeypatch, ask)
    job_id = await _queued(store, answer="подарок", clarify_round=1, no_clarify=False)

    await worker.run_job(store, job_id)
    job = await store.get(job_id)

    assert job.status is JobStatus.NO_MATCH
    assert job.result["reason"] == "needs_clarification"
    assert job.result["clarification"]["options"] == ["духи", "плед"]
    assert "Что именно ищем?" in job.result["report_markdown"]
    assert (calls[0]["answer"], calls[0]["clarify_round"]) == ("подарок", 1)


async def test_nothing_to_search_is_a_no_match_with_its_reason(store, monkeypatch) -> None:
    async def service():
        raise NothingToSearch("Это услуга, а не товар.")

    _pipeline(monkeypatch, service)
    job_id = await _queued(store)

    await worker.run_job(store, job_id)
    job = await store.get(job_id)

    assert job.status is JobStatus.NO_MATCH
    assert job.result["reason"] == "Это услуга, а не товар."


async def test_missing_substance_is_a_no_match_with_its_own_report(store, monkeypatch) -> None:
    async def no_substance():
        raise NoSubstanceMatch(["фтор"], 4, [])

    _pipeline(monkeypatch, no_substance)
    job_id = await _queued(store)

    await worker.run_job(store, job_id)
    job = await store.get(job_id)

    assert job.status is JobStatus.NO_MATCH
    assert "фтор" in job.result["reason"]
    assert job.result["report_markdown"]


async def test_empty_pool_after_filters_is_a_no_match(store, monkeypatch) -> None:
    async def empty():
        raise ValueError("no candidates passed hard filters")

    _pipeline(monkeypatch, empty)
    job_id = await _queued(store)

    await worker.run_job(store, job_id)

    job = await store.get(job_id)
    assert job.status is JobStatus.NO_MATCH
    assert job.result["reason"] == "no candidates passed hard filters"


async def test_nothing_new_is_a_no_match_with_the_reasons_and_the_olx_ads(
    store, monkeypatch
) -> None:
    ad = UsedListing(title="iPhone 13 128GB", price=180_000, new_price=373_000, saving=193_000)

    async def nothing_new():
        raise NoNewPick({"price exceeds budget": 2}, [ad])

    _pipeline(monkeypatch, nothing_new)
    job_id = await _queued(store)

    await worker.run_job(store, job_id)
    job = await store.get(job_id)

    assert job.status is JobStatus.NO_MATCH
    assert job.result["reason"] == "no candidates passed hard filters"
    assert job.result["rejected_reasons"] == {"price exceeds budget": 2}
    assert job.result["used_listings"][0]["title"] == "iPhone 13 128GB"
    report = job.result["report_markdown"]
    assert "Новых предложений нет: дороже бюджета ×2. На OLX нашлось:" in report
    assert "iPhone 13 128GB" in report


async def test_a_crash_is_failed_internal_without_the_traceback(store, monkeypatch, caplog) -> None:
    async def crash():
        raise RuntimeError("secret internals")

    _pipeline(monkeypatch, crash)
    job_id = await _queued(store)

    await worker.run_job(store, job_id)
    job = await store.get(job_id)

    assert (job.status, job.error, job.result) == (JobStatus.FAILED, "internal", None)
    assert job_id in caplog.text and "secret internals" in caplog.text


async def test_a_slow_pipeline_fails_with_timeout(store, monkeypatch) -> None:
    async def slow():
        await asyncio.sleep(5)

    _pipeline(monkeypatch, slow)
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(job_timeout_s=0.01))
    job_id = await _queued(store)

    await worker.run_job(store, job_id)

    job = await store.get(job_id)
    assert (job.status, job.error) == (JobStatus.FAILED, "timeout")


async def test_a_redelivered_finished_job_does_not_run_again(
    store, monkeypatch, recommend_result
) -> None:
    async def ok():
        return recommend_result

    calls = _pipeline(monkeypatch, ok)
    job_id = await _queued(store)

    await worker.run_job(store, job_id)
    await worker.run_job(store, job_id)

    assert len(calls) == 1


# --- auto-learning (phase 3, decision 12) ----------------------------------

CATEGORY = "соковыжималка"


@pytest.fixture
def learning(store, monkeypatch, recommend_result):
    """A finished recommend for a category with no books; ``kiq`` records what it was given."""
    recommend_result.constraints.product_type = CATEGORY
    recommend_result.knowledge_missing = True
    seen: dict = {"kiq": [], "condition": []}

    async def ok():
        return recommend_result

    _pipeline(monkeypatch, ok)

    async def condition(product_type, **kwargs):
        seen["condition"].append((product_type, kwargs))
        return True

    async def kiq(product_type):
        job = await store.get(seen["job_id"])
        seen["kiq"].append((product_type, job.status))

    monkeypatch.setattr(worker, "should_auto_learn", condition)
    monkeypatch.setattr(worker.learn_category_job, "kiq", kiq)
    return seen


async def _run(store, seen, **request) -> None:
    seen["job_id"] = await _queued(store, use_llm=True, **request)
    await worker.run_job(store, seen["job_id"])


async def test_auto_learning_is_off_by_default(store, learning) -> None:
    await _run(store, learning)

    assert learning["kiq"] == [] and learning["condition"] == []


async def test_a_new_category_is_dispatched_after_the_job_is_done(
    store, learning, monkeypatch
) -> None:
    monkeypatch.setenv("BUYSMARTER_AUTO_LEARN", "true")

    await _run(store, learning)

    assert learning["kiq"] == [(CATEGORY, JobStatus.DONE)]
    assert learning["condition"][0][1] == {
        "intent": "product",
        "use_llm": True,
        "knowledge_missing": True,
    }


async def test_the_same_category_is_dispatched_once(store, learning, monkeypatch) -> None:
    monkeypatch.setenv("BUYSMARTER_AUTO_LEARN", "true")

    await _run(store, learning)
    await _run(store, learning)

    assert [name for name, _ in learning["kiq"]] == [CATEGORY]


async def test_a_dispatch_counts_against_the_hourly_limit_where_it_is_decided(
    store, learning, monkeypatch
) -> None:
    # the learn task may run in another process: only the dispatching one checks the limit
    monkeypatch.setenv("BUYSMARTER_AUTO_LEARN", "true")

    await _run(store, learning)

    assert len(auto_learn_mod._starts) == 1


async def test_a_false_condition_dispatches_nothing(store, learning, monkeypatch) -> None:
    monkeypatch.setenv("BUYSMARTER_AUTO_LEARN", "true")

    async def no(*_a, **_k):
        return False

    monkeypatch.setattr(worker, "should_auto_learn", no)
    await _run(store, learning)

    assert learning["kiq"] == []


async def test_a_dispatch_failure_does_not_fail_the_job(
    store, learning, monkeypatch, caplog
) -> None:
    monkeypatch.setenv("BUYSMARTER_AUTO_LEARN", "true")

    async def broken(_name):
        raise ConnectionError("broker down")

    monkeypatch.setattr(worker.learn_category_job, "kiq", broken)
    await _run(store, learning)

    assert (await store.get(learning["job_id"])).status is JobStatus.DONE
    assert any(CATEGORY in r.getMessage() and r.exc_info for r in caplog.records)


async def test_the_learn_task_runs_the_shared_runner(monkeypatch) -> None:
    ran: list[str] = []

    async def runner(name):
        ran.append(name)
        return True

    monkeypatch.setattr(worker, "auto_learn", runner)
    await worker.learn_category_job(CATEGORY)

    assert ran == [CATEGORY]


async def test_a_learn_over_its_timeout_is_marked_failed(monkeypatch) -> None:
    marked: list[str] = []

    async def forever(_name):
        await asyncio.sleep(10)

    async def failed(name):
        marked.append(name)

    monkeypatch.setattr(worker, "auto_learn", forever)
    monkeypatch.setattr(worker, "mark_learn_failed", failed)
    monkeypatch.setattr(worker, "LEARN_TIMEOUT_S", 0.01)
    await worker.learn_category_job(CATEGORY)

    assert marked == [CATEGORY]
