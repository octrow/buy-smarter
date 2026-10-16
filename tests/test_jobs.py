"""JobStore: the job record, request dedupe and the per-client rate limit, all in Redis."""

import pytest
from fakeredis import FakeAsyncRedis

from buysmarter.jobs import JobStatus, JobStore, RecommendRequest, dedupe_key


@pytest.fixture
def store() -> JobStore:
    return JobStore(FakeAsyncRedis(decode_responses=True))


async def test_a_new_job_is_queued_with_its_request(store: JobStore) -> None:
    request = RecommendRequest(query="ирригатор", budget=40000)

    job = await store.create(request, dedupe="d1")
    loaded = await store.get(job.id)

    assert loaded is not None
    assert loaded.status is JobStatus.QUEUED
    assert loaded.request == request
    assert loaded.created_at is not None and loaded.started_at is None


async def test_unknown_job_is_none(store: JobStore) -> None:
    assert await store.get("nope") is None


async def test_start_runs_a_queued_or_interrupted_job_but_never_a_finished_one(store) -> None:
    job = await store.create(RecommendRequest(query="q"), dedupe="d")

    assert await store.start(job.id) is True
    assert (await store.get(job.id)).status is JobStatus.RUNNING
    assert await store.start(job.id) is True  # redelivered after a worker crash

    await store.finish(job.id, JobStatus.DONE, result={"report_markdown": "# r"})
    assert await store.start(job.id) is False
    assert await store.start("missing") is False


async def test_finish_stores_the_result_and_the_end_time(store: JobStore) -> None:
    job = await store.create(RecommendRequest(query="q"), dedupe="d")
    await store.start(job.id)

    await store.finish(job.id, JobStatus.DONE, result={"report_markdown": "# r"})
    loaded = await store.get(job.id)

    assert loaded.status is JobStatus.DONE
    assert loaded.result == {"report_markdown": "# r"}
    assert loaded.finished_at is not None and loaded.error is None


async def test_job_record_expires(store: JobStore) -> None:
    job = await store.create(RecommendRequest(query="q"), dedupe="d")

    ttl = await store.redis.ttl(f"buysmarter:job:{job.id}")

    assert 0 < ttl <= 24 * 3600


async def test_dedupe_returns_the_first_job_for_the_same_request(store: JobStore) -> None:
    assert await store.claim_dedupe("k", "job-1") is None
    assert await store.claim_dedupe("k", "job-2") == "job-1"


async def test_a_failed_job_frees_its_dedupe_slot(store: JobStore) -> None:
    job = await store.create(RecommendRequest(query="q"), dedupe="k")
    await store.claim_dedupe("k", job.id)

    await store.finish(job.id, JobStatus.FAILED, error="internal")

    assert await store.claim_dedupe("k", "job-2") is None


def test_dedupe_key_ignores_case_spacing_and_source_order() -> None:
    a = RecommendRequest(query="  Ирригатор ", sources=["olx", "kaspi"])
    b = RecommendRequest(query="ирригатор", sources=["kaspi", "olx"])
    c = RecommendRequest(query="ирригатор", sources=["kaspi"])

    assert dedupe_key(a) == dedupe_key(b) != dedupe_key(c)


async def test_rate_limit_allows_up_to_the_limit_then_says_when_to_retry(store) -> None:
    results = [await store.rate_limit("1.2.3.4", limit=3, window_s=600) for _ in range(4)]

    assert results[:3] == [None, None, None]
    assert 0 < results[3] <= 600
    assert await store.rate_limit("5.6.7.8", limit=3, window_s=600) is None
