"""POST /recommend, GET /recommend/{id}, GET /history: queueing, dedupe, limits, auth, errors."""

import time

import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from buysmarter import worker
from buysmarter.api import main, recommend
from buysmarter.jobs import JobStore


@pytest.fixture
def redis(monkeypatch):
    fake = FakeAsyncRedis(decode_responses=True)
    monkeypatch.setattr(worker, "get_redis", lambda: fake)
    main.app.dependency_overrides[recommend.get_store] = lambda: JobStore(fake)
    yield fake
    main.app.dependency_overrides.clear()


@pytest.fixture
def client(redis, monkeypatch, recommend_result):
    async def fake_recommend(query, **kwargs):
        return recommend_result

    monkeypatch.setattr(worker, "recommend", fake_recommend)
    monkeypatch.delenv("BUYSMARTER_API_KEYS", raising=False)
    with TestClient(main.app) as test_client:
        yield test_client


def _poll(client, job_id: str, attempts: int = 100) -> dict:
    """Poll like a real client: the in-memory broker runs the job on the app's own event loop."""
    for _ in range(attempts):
        job = client.get(f"/recommend/{job_id}").json()
        if job["status"] in {"done", "no_match", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} still {job['status']}")


def _post(client, **body):
    return client.post("/recommend", json={"query": "ирригатор до 40000", **body})


def test_post_queues_a_job_and_points_at_its_status(client) -> None:
    response = _post(client)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["status_url"] == f"/recommend/{body['job_id']}"
    assert response.headers["location"] == body["status_url"]


def test_the_worker_finishes_the_job_and_get_shows_the_outcome(client) -> None:
    job_id = _post(client).json()["job_id"]

    job = _poll(client, job_id)

    assert job["status"] == "done"
    assert job["result"]["best"]["title"] == "Waterpik WP-100 ирригатор"
    assert job["request"]["sources"] == ["kaspi", "olx"]
    assert job["finished_at"] is not None


def test_unknown_job_is_404(client) -> None:
    assert client.get("/recommend/nope").status_code == 404


@pytest.mark.parametrize(
    "body",
    [{"query": ""}, {"budget": 0}, {"top": 100}, {"sources": ["amazon"]}, {"sources": ["ozon"]}],
)
def test_invalid_requests_are_422(client, body) -> None:
    assert _post(client, **body).status_code == 422


def test_ozon_is_accepted_where_the_worker_has_chromium(client, monkeypatch) -> None:
    monkeypatch.setenv("BUYSMARTER_SOURCES", "kaspi,ozon,olx")

    assert _post(client, sources=["ozon"]).status_code == 202


def test_the_same_request_returns_the_same_job(client) -> None:
    first = _post(client)
    again = _post(client, query="  Ирригатор до 40000 ")

    assert again.status_code == 200
    assert again.json()["job_id"] == first.json()["job_id"]


def test_too_many_new_jobs_are_429_with_retry_after(client, monkeypatch) -> None:
    monkeypatch.setenv("BUYSMARTER_RATE_LIMIT_JOBS", "2")

    codes = [_post(client, query=f"товар {i}").status_code for i in range(3)]
    over = _post(client, query="ещё один")

    assert codes == [202, 202, 429]
    assert over.status_code == 429 and int(over.headers["retry-after"]) > 0


def test_api_key_is_required_once_keys_are_configured(client, monkeypatch) -> None:
    monkeypatch.setenv("BUYSMARTER_API_KEYS", "k1, k2")

    assert _post(client).status_code == 401
    assert client.get("/recommend/x", headers={"X-API-Key": "bad"}).status_code == 401
    assert (
        client.post("/recommend", json={"query": "q"}, headers={"X-API-Key": "k2"}).status_code
        == 202
    )


def test_redis_outage_is_503(client, redis, monkeypatch) -> None:
    async def down(*_args, **_kwargs):
        raise RedisConnectionError("refused")

    monkeypatch.setattr(redis, "get", down)

    response = _post(client)

    assert response.status_code == 503
    assert "refused" not in response.text


def test_history_wraps_the_price_history_service(client, monkeypatch) -> None:
    async def fake_history(term):
        return []

    monkeypatch.setattr(recommend, "product_history", fake_history)

    assert client.get("/history", params={"q": "wp-100"}).json() == []
    assert client.get("/history", params={"q": "w"}).status_code == 422
