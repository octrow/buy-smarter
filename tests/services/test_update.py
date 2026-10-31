"""Updates: a checkout fast-forwards to main or a release tag, and only when that is safe."""

import subprocess
from pathlib import Path

import pytest

from buysmarter.services import update
from buysmarter.services.update import UpdateSettings

CODE = UpdateSettings(channel="code")
RELEASES = UpdateSettings(channel="releases")


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", name)


def _as_user(repo: Path) -> None:
    for key, value in (("user.email", "t@t"), ("user.name", "t")):
        _git(repo, "config", key, value)


@pytest.fixture
def repos(tmp_path):
    """(the GitHub side, the user's clone), both on main with one commit."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-q", "-b", "main")
    _as_user(upstream)
    _commit(upstream, "a")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True)
    _as_user(clone)
    return upstream, clone


def test_a_clone_behind_main_takes_the_new_commit(repos):
    upstream, clone = repos
    assert not update.check(CODE, "checkout", clone).available
    _commit(upstream, "b")

    status = update.check(CODE, "checkout", clone)
    assert status.available and status.can_apply

    assert update.apply(CODE, "checkout", clone).startswith("Обновлено")
    assert (clone / "b").exists()
    assert not update.check(CODE, "checkout", clone).available


def test_uncommitted_changes_hold_the_update(repos):
    upstream, clone = repos
    _commit(upstream, "b")
    (clone / "a").write_text("edited")

    status = update.check(CODE, "checkout", clone)
    assert status.available and not status.can_apply
    assert "незакоммиченные" in update.apply(CODE, "checkout", clone)
    assert not (clone / "b").exists()


def test_unpushed_commits_hold_the_update(repos):
    upstream, clone = repos
    _commit(upstream, "b")
    _commit(clone, "mine")

    status = update.check(CODE, "checkout", clone)
    assert not status.can_apply and "неотправленные" in status.note


def test_the_releases_channel_stops_at_the_latest_tag(repos, monkeypatch):
    upstream, clone = repos
    _commit(upstream, "b")
    _git(upstream, "tag", "v9.0.0")
    _commit(upstream, "c")
    monkeypatch.setattr(update, "latest_release", lambda: "9.0.0")

    assert (
        update.apply(RELEASES, "checkout", clone) == "Обновлено до 9.0.0. Перезапустите программу."
    )
    assert (clone / "b").exists() and not (clone / "c").exists()
    assert not update.check(RELEASES, "checkout", clone).available


def test_a_uv_tool_install_compares_versions(monkeypatch):
    monkeypatch.setattr(update, "latest_release", lambda: "999.0.0")
    assert update.check(RELEASES, "uv-tool").available
    monkeypatch.setattr(update, "latest_release", lambda: "0.0.1")
    assert not update.check(RELEASES, "uv-tool").available


def test_the_frozen_build_only_points_at_the_release_page(monkeypatch):
    monkeypatch.setattr(update, "latest_release", lambda: "999.0.0")
    status = update.check(RELEASES, "frozen")
    assert status.available and not status.can_apply and update.RELEASES_URL in status.note
    assert update.channels_for("frozen") == ("releases",)


def test_settings_round_trip_and_fall_back_to_releases_where_code_is_unavailable():
    update.save_settings(UpdateSettings(channel="code", auto=False))
    assert update.load_settings("checkout") == UpdateSettings(channel="code", auto=False)
    assert update.load_settings("frozen").channel == "releases"
