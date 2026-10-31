"""Shared test fixtures."""

import logging
import os
import sqlite3
import tempfile
from pathlib import Path

# Before any test imports buysmarter.worker: an in-process broker, no Redis stream.
os.environ.setdefault("BUYSMARTER_BROKER", "memory")
# Auto-learning is off in the API/worker unless a test turns it on.
os.environ["BUYSMARTER_AUTO_LEARN"] = "false"

# Before any buysmarter import (db.base builds its engine at import): the whole suite uses one tmp
# SQLite store and tmp legacy paths, never the developer's data dir, Postgres or ~/.config files.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="buysmarter-tests-"))
TEST_DB = _TEST_ROOT / "test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"
os.environ["BUYSMARTER_PREFS_FILE"] = str(_TEST_ROOT / "legacy" / "prefs.json")
os.environ["BUYSMARTER_KNOWLEDGE_DIR"] = str(_TEST_ROOT / "legacy" / "knowledge")
os.environ["BUYSMARTER_STYLE_DIR"] = str(_TEST_ROOT / "style")
os.environ["BUYSMARTER_UPDATE_FILE"] = str(_TEST_ROOT / "update.json")
# The GUI tests run headless.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import importlib.util  # noqa: E402 - the env above must be set first

import keyring  # noqa: E402
import pytest  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402
from keyring.errors import PasswordDeleteError  # noqa: E402

# Tests of the HTTP API, the worker and the job queue need the [server] extra.
SERVER_TESTS = ("api/*", "test_jobs.py", "test_worker.py", "test_telemetry.py")
_HAS_SERVER_EXTRA = all(importlib.util.find_spec(m) for m in ("fastapi", "redis", "taskiq"))
collect_ignore_glob = [] if _HAS_SERVER_EXTRA else list(SERVER_TESTS)
if not all(importlib.util.find_spec(m) for m in ("onnxruntime", "tokenizers", "PIL", "numpy")):
    collect_ignore_glob.append("style")  # the `style` extra is not installed
# The desktop app tests need the [gui] extra.
if not importlib.util.find_spec("PySide6"):
    collect_ignore_glob.append("gui")  # the directory itself: its conftest imports PySide6


def pytest_collection_modifyitems(config, items):
    """Mark the server tests, so `-m "not server"` selects the core suite."""
    root = Path(__file__).parent
    for item in items:
        rel = item.path.relative_to(root)
        if any(rel.match(pattern) for pattern in SERVER_TESTS):
            item.add_marker(pytest.mark.server)


@pytest.fixture(scope="session", autouse=True)
def _test_store():
    """Migrate the tmp store once for the session."""
    from buysmarter.db.schema import ensure_schema

    ensure_schema()
    yield TEST_DB


@pytest.fixture(autouse=True)
def _empty_store(_test_store):
    """Every test starts on empty tables (the schema stays)."""
    from buysmarter.db.base import Base

    with sqlite3.connect(_test_store) as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(f"DELETE FROM {table.name}")  # noqa: S608 - names from our own metadata


@pytest.fixture(autouse=True)
def _isolate_file_logging(tmp_path, monkeypatch):
    """Keep test log records out of the real log file.

    Tests that exercise the CLI call setup_logging(); point it at a tmp file
    and drop any file handlers left on the root logger afterwards.
    """
    from buysmarter import logsetup
    from buysmarter.cli import main as cli_main

    orig = logsetup.setup_logging

    def _to_tmp(log_path=None, **kw):
        return orig(log_path=tmp_path / "test.log", **kw)

    monkeypatch.setattr(logsetup, "setup_logging", _to_tmp)
    monkeypatch.setattr(cli_main, "setup_logging", _to_tmp)
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler):
            root.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def _no_live_claude(monkeypatch):
    """A test that forgets to mock the CLI fails loudly instead of calling the live gateway."""
    from buysmarter.llm import adapter

    async def _blocked(*_args, **_kwargs):
        raise RuntimeError("test reached the live claude CLI: mock adapter.arun_claude")

    monkeypatch.setattr(adapter, "arun_claude", _blocked)
    # The LLM route must not depend on whether this machine has the CLI (CI does not).
    monkeypatch.setattr(adapter, "claude_available", lambda: False)


@pytest.fixture(autouse=True)
def _no_auto_learn(monkeypatch):
    """No test starts a real learn: the classifier says "no CLI" and a learn itself is blocked."""
    from buysmarter.services import auto_learn

    async def _blocked(*_args, **_kwargs):
        raise RuntimeError("test reached a live learn: mock auto_learn.learn_category")

    monkeypatch.setattr(auto_learn, "claude_available", lambda: False)
    monkeypatch.setattr(auto_learn, "learn_category", _blocked)
    auto_learn.reset_limit()


@pytest.fixture(autouse=True)
def _no_live_browser(monkeypatch, tmp_path):
    """No test launches a browser or sees the developer's Ozon session: none installed or saved."""
    from buysmarter.services import ozon_browser

    monkeypatch.setattr(ozon_browser, "launches", lambda browser: False)
    monkeypatch.setenv("OZON_STATE_PATH", str(tmp_path / "ozon_state.json"))


@pytest.fixture(autouse=True)
def _no_live_kimovil(monkeypatch, tmp_path):
    """Smartphone queries never reach kimovil.com: every lookup is a miss, cached in tmp."""
    from market_sources.kimovil.source import KimovilSource

    async def _miss(self, title):
        return None

    monkeypatch.setattr(KimovilSource, "lookup", _miss)
    monkeypatch.setenv("KIMOVIL_CACHE_PATH", str(tmp_path / "kimovil.db"))


@pytest.fixture(autouse=True)
def _no_live_jev(monkeypatch):
    """Jev never goes over HTTP in tests: an unmocked call behaves as "no OmniRoute key".

    That is a real production state (``LlmConfigError``), so call sites take their
    fallback — tests that stub only ``generate`` exercise the LLM fallback path.
    """
    from buysmarter.llm import jev
    from buysmarter.llm.adapter import LlmConfigError

    def _no_key():
        raise LlmConfigError("Jev is off in tests: mock <module>.system_one")

    monkeypatch.setattr(jev, "api_key", _no_key)


@pytest.fixture(autouse=True)
def _no_classifier(monkeypatch):
    """The capability classifier asks nothing unless a test switches it on.

    Tests from before phase 3 fake Jev for their own questions only; a classifier
    question in their call would change what they record.
    """
    from buysmarter.capabilities import classify

    monkeypatch.setattr(classify, "capability_questions", dict)


class MemoryKeyring(KeyringBackend):
    """An in-memory OS credential store: no test reads or writes the developer's real keyring."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.secrets: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.secrets.get((service, username))

    def set_password(self, service, username, password):
        self.secrets[(service, username)] = password

    def delete_password(self, service, username):
        if self.secrets.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


@pytest.fixture(autouse=True)
def fake_keyring():
    # No keyring.get_keyring() to restore it: that would probe the real Secret Service.
    from buysmarter.llm import keys

    store = MemoryKeyring()
    keyring.set_keyring(store)
    keys.stored.cache_clear()
    return store


@pytest.fixture(autouse=True)
def _isolate_reports(tmp_path, monkeypatch):
    """Reports written without an explicit path go to tmp, not the user's data dir."""
    from buysmarter.cli import report

    monkeypatch.setattr(report, "_REPORTS_DIR", tmp_path / "reports")


@pytest.fixture
def recommend_result():
    """A minimal finished recommend run: one Kaspi candidate, Almaty, 40 000 ₸ budget."""
    from buysmarter.ranking.models import (
        AxisScore,
        Candidate,
        QueryConstraints,
        Recommendation,
        ScoredCandidate,
    )
    from buysmarter.services.recommend import RecommendResult

    cand = Candidate(
        source="kaspi",
        source_product_id="k1",
        title="Waterpik WP-100 ирригатор",
        brand="Waterpik",
        price=30000,
        url="https://kaspi.kz/shop/p/k1",
    )
    scored = ScoredCandidate(
        candidate=cand,
        total_score=88.0,
        confidence=0.9,
        axes=[AxisScore(name="brand", score=25.0, max_score=25, explanation="tier A")],
        pros=["надёжный бренд"],
    )
    rec = Recommendation(best=scored, ranked=[scored])
    constraints = QueryConstraints(search_phrase="ирригатор", city="Алматы", budget_max=40000)
    return RecommendResult(recommendation=rec, constraints=constraints)


@pytest.fixture
def photo(tmp_path):
    """A JPEG with camera EXIF; ``photo(name, colour)`` writes another one."""
    image = pytest.importorskip("PIL.Image")

    def make(name: str = "look.jpg", colour: str = "navy"):
        exif = image.Exif()
        exif[0x010F] = "SecretCam"  # Make
        path = tmp_path / name
        image.new("RGB", (64, 48), colour).save(path, exif=exif)
        return path

    return make


@pytest.fixture(autouse=True)
def _no_shoe_cropping(monkeypatch):
    """No segmenter in tests: sample photos are not cut (a test sets its own cropper)."""
    from buysmarter.style import profile

    async def none():
        return None

    monkeypatch.setattr(profile, "shoe_cropper", none)


@pytest.fixture
def encoder(monkeypatch):
    """No model download: every photo encodes to the same unit vector."""
    np = pytest.importorskip("numpy")
    from buysmarter.style import profile

    async def fake():
        return lambda blobs: np.ones((len(blobs), 4), dtype=np.float32) / 2

    monkeypatch.setattr(profile, "photo_encoder", fake)
