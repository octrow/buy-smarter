"""Shared test fixtures."""

import logging
import os

# Before any test imports buysmarter.worker: an in-process broker, no Redis stream.
os.environ.setdefault("BUYSMARTER_BROKER", "memory")

import pytest


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
def _isolate_knowledge_store(tmp_path, monkeypatch):
    """Keep tests off the user's real knowledge (Postgres and ~/.config/buysmarter/
    knowledge): stored categories feed slug matching and would reach the LLM."""

    def _no_db(*_args, **_kwargs):
        raise RuntimeError("DB disabled in test — use file fallback")

    monkeypatch.setenv("BUYSMARTER_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setattr("buysmarter.knowledge.store.async_session", _no_db)


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
    constraints = QueryConstraints(category="ирригатор", city="Алматы", budget_max=40000)
    return RecommendResult(recommendation=rec, constraints=constraints)
