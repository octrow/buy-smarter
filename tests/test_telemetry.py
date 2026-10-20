"""/metrics exposes HTTP and domain metrics in the Prometheus text format."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from buysmarter.api import main  # sets the global meter provider
from buysmarter.services import collect
from buysmarter.services import recommend as svc

client = TestClient(main.app)


def _sample(name: str, **labels: str) -> float:
    """Current value of one sample on /metrics; 0 when it has not been recorded yet."""
    text = client.get("/metrics").text
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name == name and labels.items() <= sample.labels.items():
                return sample.value
    return 0.0


def test_metrics_endpoint_serves_request_metrics_but_skips_probes() -> None:
    client.get("/openapi.json")
    client.get("/health")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'http_route="/openapi.json"' in response.text
    assert 'http_route="/health"' not in response.text


async def test_source_requests_are_counted_by_outcome() -> None:
    async def ok():
        return []

    async def boom():
        raise TimeoutError

    before_ok = _sample("buysmarter_source_requests_total", source="kaspi", outcome="ok")
    before_err = _sample("buysmarter_source_requests_total", source="ozon", outcome="error")

    await collect._safe("kaspi", ok())
    await collect._safe("ozon", boom())

    assert (
        _sample("buysmarter_source_requests_total", source="kaspi", outcome="ok") == before_ok + 1
    )
    assert (
        _sample("buysmarter_source_requests_total", source="ozon", outcome="error")
        == before_err + 1
    )


async def test_recommend_duration_and_ranked_offers_are_recorded() -> None:
    @svc._measured
    async def fake_recommend():
        return SimpleNamespace(recommendation=SimpleNamespace(ranked=[1, 2, 3]))

    count = _sample("buysmarter_recommend_duration_seconds_count", outcome="ok")
    offers = _sample("buysmarter_recommend_offers_ranked_sum")

    await fake_recommend()

    assert _sample("buysmarter_recommend_duration_seconds_count", outcome="ok") == count + 1
    assert _sample("buysmarter_recommend_offers_ranked_sum") == offers + 3


async def test_recommend_without_a_substance_match_is_its_own_outcome() -> None:
    @svc._measured
    async def fake_recommend():
        raise svc.NoSubstanceMatch(["fluoride"], 5, [])

    count = _sample("buysmarter_recommend_duration_seconds_count", outcome="no_match")

    with pytest.raises(svc.NoSubstanceMatch):
        await fake_recommend()

    assert _sample("buysmarter_recommend_duration_seconds_count", outcome="no_match") == count + 1


async def test_llm_calls_are_counted_as_cache_hits_and_misses(monkeypatch) -> None:
    from pydantic import BaseModel

    from buysmarter.config import Settings
    from buysmarter.llm import adapter

    class Answer(BaseModel):
        name: str

    async def cached(_key):
        return {"name": "from cache"}

    async def nothing_cached(_key):
        return None

    async def fake_cli(*_args, **_kwargs):
        return Answer(name="fresh")

    settings = Settings(_env_file=None, default_llm_model="sonnet")
    monkeypatch.setattr(adapter, "_generate_cli", fake_cli)
    monkeypatch.setattr(adapter, "claude_available", lambda: True)
    hits = _sample("buysmarter_llm_calls_total", model="sonnet", cache="hit")
    misses = _sample("buysmarter_llm_calls_total", model="sonnet", cache="miss")

    monkeypatch.setattr(adapter, "get_cached", cached)
    await adapter.generate("p", Answer, settings=settings)
    monkeypatch.setattr(adapter, "get_cached", nothing_cached)
    await adapter.generate("p", Answer, settings=settings)

    assert _sample("buysmarter_llm_calls_total", model="sonnet", cache="hit") == hits + 1
    assert _sample("buysmarter_llm_calls_total", model="sonnet", cache="miss") == misses + 1


async def test_finished_jobs_and_queue_wait_are_recorded(monkeypatch, recommend_result) -> None:
    from fakeredis import FakeAsyncRedis

    from buysmarter import worker
    from buysmarter.jobs import JobStore, RecommendRequest

    async def fake_recommend(query, **kwargs):
        return recommend_result

    monkeypatch.setattr(worker, "recommend", fake_recommend)
    store = JobStore(FakeAsyncRedis(decode_responses=True))
    job = await store.create(RecommendRequest(query="q", sources=["kaspi"]), dedupe="d")
    done = _sample("buysmarter_jobs_total", status="done")
    waits = _sample("buysmarter_job_wait_seconds_count")

    await worker.run_job(store, job.id)

    assert _sample("buysmarter_jobs_total", status="done") == done + 1
    assert _sample("buysmarter_job_wait_seconds_count") == waits + 1
