"""``--self-test``: what CI runs against each built desktop app, here run from source."""

import os
import subprocess
import sys

_TIMEOUT_S = 120


def _self_test(*extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "buysmarter.gui", "--self-test", *extra],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
        env=env,
        check=False,
    )


def test_self_test_passes_from_source():
    done = _self_test()
    assert done.returncode == 0, done.stderr
    assert "self-test ok" in done.stdout


def test_self_test_leaves_the_configured_store_alone(tmp_path):
    store = tmp_path / "real.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{store}"}
    assert _self_test(env=env).returncode == 0
    assert not store.exists()
