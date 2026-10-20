"""Isolation for the local-storage tests: every scenario runs in a subprocess with a clean env.

HOME and the XDG dirs point at a tmp path, ``DATABASE_URL`` is unset (so the default URL under
the per-user data dir is what gets resolved), and the cwd is empty, so no test can touch the real
~/.local/share/buysmarter, a Postgres, or the repo's alembic.ini.
"""

import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pytest

SUBPROCESS_TIMEOUT_S = 120

# Server-only packages: a core install must not need any of them. uvicorn is not blocked: fastmcp
# (a core dependency) imports it, so a plain install has it anyway.
BLOCK_SERVER_DEPS = """
import sys
_BLOCKED = ("fastapi", "asyncpg", "redis", "taskiq", "taskiq_redis",
            "prometheus_client", "opentelemetry.instrumentation.fastapi",
            "opentelemetry.exporter.prometheus")

class _Block:
    def find_spec(self, name, path=None, target=None):
        if any(name == b or name.startswith(b + ".") for b in _BLOCKED):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)

sys.meta_path.insert(0, _Block())
"""


PRODUCT = """
from market_sources.base import RawProduct
P = RawProduct(source="kaspi", product_id="k1", title="Waterpik WP-100",
               url="https://kaspi.kz/shop/p/k1", price=30000)
"""


def _expand(code: str) -> str:
    """Dedent first, then splice the column-0 snippets (``__PRODUCT__``, ``__BLOCK__``)."""
    return (
        textwrap.dedent(code)
        .replace("__PRODUCT__", PRODUCT)
        .replace("__BLOCK__", BLOCK_SERVER_DEPS)
    )


@dataclass
class Store:
    root: Path
    env: dict[str, str] = field(default_factory=dict)

    @property
    def data_dir(self) -> Path:
        return self.root / "xdg_data" / "buysmarter"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "buysmarter.db"

    @property
    def prefs_file(self) -> Path:
        return self.root / "legacy" / "prefs.json"

    @property
    def knowledge_dir(self) -> Path:
        return self.root / "legacy" / "knowledge"

    def run(
        self, code: str, *, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        cwd = self.root / "cwd"
        cwd.mkdir(exist_ok=True)
        return subprocess.run(
            [sys.executable, "-c", _expand(code)],
            env={**self.env, **(extra_env or {})},
            cwd=cwd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=False,
        )

    def run_ok(self, code: str, **kw: object) -> subprocess.CompletedProcess[str]:
        proc = self.run(code, **kw)  # type: ignore[arg-type]
        assert proc.returncode == 0, f"subprocess failed:\n{proc.stderr[-2000:]}"
        return proc


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Nothing in this directory may see a developer's DATABASE_URL or data dir."""
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def store(tmp_path) -> Store:
    keep = {
        k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "VIRTUAL_ENV", "UV_CACHE_DIR"}
    }
    env = {
        **keep,
        "HOME": str(tmp_path / "home"),
        "XDG_DATA_HOME": str(tmp_path / "xdg_data"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg_config"),
        "XDG_CACHE_HOME": str(tmp_path / "xdg_cache"),
        "BUYSMARTER_BROKER": "memory",
        "BUYSMARTER_PREFS_FILE": str(tmp_path / "legacy" / "prefs.json"),
        "BUYSMARTER_KNOWLEDGE_DIR": str(tmp_path / "legacy" / "knowledge"),
        "KIMOVIL_CACHE_PATH": str(tmp_path / "kimovil.db"),
        "COLUMNS": "400",
        "NO_COLOR": "1",
    }
    return Store(root=tmp_path, env=env)
