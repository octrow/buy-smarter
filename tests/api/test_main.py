"""HTTP probes: /health never touches dependencies, /ready reflects the database."""

from fastapi.testclient import TestClient

import buysmarter
from buysmarter.api import main


async def _done(value):
    return value


def test_health_is_ok_without_a_database(monkeypatch) -> None:
    monkeypatch.setattr(main, "db_reachable", lambda url: _done(False))

    response = TestClient(main.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_is_ok_when_the_database_answers(monkeypatch) -> None:
    monkeypatch.setattr(main, "db_reachable", lambda url: _done(True))
    monkeypatch.setattr(main, "redis_reachable", lambda: _done(True))

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_is_503_when_the_database_is_down(monkeypatch) -> None:
    monkeypatch.setattr(main, "db_reachable", lambda url: _done(False))
    monkeypatch.setattr(main, "redis_reachable", lambda: _done(True))

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "database unavailable"}


def test_ready_is_503_when_redis_is_down(monkeypatch) -> None:
    monkeypatch.setattr(main, "db_reachable", lambda url: _done(True))
    monkeypatch.setattr(main, "redis_reachable", lambda: _done(False))

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "redis unavailable"}


def test_openapi_reports_the_package_version() -> None:
    assert main.app.version == buysmarter.__version__ != "0.1.0"
