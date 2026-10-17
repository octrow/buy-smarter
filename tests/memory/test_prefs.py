"""prefs JSON fallback: non-object JSON must be treated as empty."""

import json

import pytest

from buysmarter.memory import prefs


def test_read_file_non_object_json_is_empty(tmp_path, monkeypatch, caplog) -> None:
    path = tmp_path / "prefs.json"
    path.write_text("[1, 2]", encoding="utf-8")
    monkeypatch.setenv("BUYSMARTER_PREFS_FILE", str(path))
    with caplog.at_level("WARNING"):
        assert prefs._read_file() == {}
    assert "not a JSON object" in caplog.text


class _DbDown:
    async def __aenter__(self) -> None:
        raise OSError("connection refused")

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    path = tmp_path / "prefs.json"
    monkeypatch.setenv("BUYSMARTER_PREFS_FILE", str(path))
    return path


@pytest.fixture
def db_down(monkeypatch):
    monkeypatch.setattr(prefs, "async_session", _DbDown)


async def test_set_then_get_round_trips_through_file_when_db_down(prefs_file, db_down) -> None:
    await prefs.set("city", "Алматы")
    await prefs.set("budget", 40000)

    assert await prefs.get("city") == "Алматы"
    assert await prefs.get("missing") is None
    assert await prefs.get_all() == {"city": "Алматы", "budget": 40000}
    assert json.loads(prefs_file.read_text(encoding="utf-8"))["city"] == "Алматы"


async def test_get_all_is_empty_when_db_down_and_no_file(prefs_file, db_down) -> None:
    assert await prefs.get_all() == {}


def test_read_file_corrupt_json_is_empty(prefs_file, caplog) -> None:
    prefs_file.write_text("{not json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        assert prefs._read_file() == {}
    assert "unreadable" in caplog.text


async def test_db_reachable_reads_and_writes_go_to_repo_not_file(prefs_file, monkeypatch) -> None:
    stored: dict[str, object] = {}

    async def set_preference(session, key, value) -> None:
        stored[key] = value

    async def get_preference(session, key):
        return stored.get(key)

    async def get_all_preferences(session):
        return dict(stored)

    monkeypatch.setattr(prefs, "async_session", _FakeSession)
    monkeypatch.setattr(prefs.repo, "set_preference", set_preference)
    monkeypatch.setattr(prefs.repo, "get_preference", get_preference)
    monkeypatch.setattr(prefs.repo, "get_all_preferences", get_all_preferences)

    await prefs.set("city", "Астана")

    assert await prefs.get("city") == "Астана"
    assert await prefs.get_all() == {"city": "Астана"}
    assert not prefs_file.exists()
