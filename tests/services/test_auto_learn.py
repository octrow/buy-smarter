"""The auto-learning condition (phase 3, decision 8): each clause on its own."""

from datetime import UTC, datetime, timedelta

import pytest

from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.store import load_knowledge, save_knowledge, update_knowledge
from buysmarter.services import auto_learn
from buysmarter.services.auto_learn import (
    LEARN_FAILED_KEY,
    LEARNING_STARTED_KEY,
    auto_learn as run_auto_learn,
    mark_learn_failed,
    mark_learning_started,
    should_auto_learn,
)

CATEGORY = "соковыжималка"


@pytest.fixture(autouse=True)
def _cli_and_empty_limit(monkeypatch):
    """The claude CLI is there and the hourly counter starts empty."""
    monkeypatch.setattr(auto_learn, "claude_available", lambda: True)
    auto_learn.reset_limit()


async def _ok(category: str = CATEGORY, **kw) -> bool:
    args = {"intent": "product", "use_llm": True, "knowledge_missing": True, **kw}
    return await should_auto_learn(category, **args)


async def _ago(key: str, delta: timedelta) -> None:
    stamp = (datetime.now(UTC) - delta).isoformat()
    await update_knowledge(CATEGORY, {key: stamp}, "test")


async def test_a_new_category_with_everything_in_place_is_learned() -> None:
    assert await _ok()


async def test_a_profile_with_books_is_not_learned_again() -> None:
    await save_knowledge(CATEGORY, BrandBook(tiers={"Philips": "A"}), None, "opus")

    assert not await _ok()


async def test_books_found_for_the_request_elsewhere_are_not_learned_again() -> None:
    # a synonym, a parent or a related profile ("солнцезащитный крем" → "косметика")
    assert not await _ok(knowledge_missing=False)


async def test_a_record_without_books_is_still_learned() -> None:
    await update_knowledge(CATEGORY, {"capabilities": {}}, "jev")

    assert await _ok()


@pytest.mark.parametrize("intent", ["product", "need"])
async def test_product_and_need_intents_are_learned(intent: str) -> None:
    assert await _ok(intent=intent)


@pytest.mark.parametrize("intent", ["service", "unclear", "open"])
async def test_other_intents_are_not_learned(intent: str) -> None:
    assert not await _ok(intent=intent)


async def test_a_parse_without_the_llm_is_not_learned() -> None:
    assert not await _ok(use_llm=False)


async def test_without_the_claude_cli_nothing_is_learned(monkeypatch) -> None:
    monkeypatch.setattr(auto_learn, "claude_available", lambda: False)

    assert not await _ok()


@pytest.mark.parametrize("garbage", ["", "   ", "ноут; rm -rf /", "что?!", "a/b", "<b>x</b>"])
async def test_a_garbage_product_type_is_not_learned(garbage: str) -> None:
    assert not await _ok(garbage)


async def test_a_too_long_product_type_is_not_learned() -> None:
    assert await _ok("а" * 60)
    assert not await _ok("а" * 61)


@pytest.mark.parametrize("name", ["робот-пылесос", "iphone 15", "Шампунь 2"])
async def test_letters_digits_space_and_hyphen_pass(name: str) -> None:
    assert await _ok(name)


async def test_a_learn_started_minutes_ago_blocks_and_after_15_minutes_does_not() -> None:
    await _ago(LEARNING_STARTED_KEY, timedelta(minutes=14))
    assert not await _ok()

    await _ago(LEARNING_STARTED_KEY, timedelta(minutes=16))
    assert await _ok()


async def test_a_failure_blocks_for_24_hours() -> None:
    await _ago(LEARN_FAILED_KEY, timedelta(hours=23))
    assert not await _ok()

    await _ago(LEARN_FAILED_KEY, timedelta(hours=25))
    assert await _ok()


async def test_a_broken_mark_does_not_block() -> None:
    await update_knowledge(CATEGORY, {LEARN_FAILED_KEY: "not a date"}, "test")

    assert await _ok()


async def test_over_the_hourly_limit_nothing_is_learned(monkeypatch) -> None:
    monkeypatch.setenv("BUYSMARTER_AUTO_LEARN_PER_HOUR", "2")
    await mark_learning_started("чайник")
    assert await _ok()

    await mark_learning_started("тостер")

    assert not await _ok()


async def test_the_hourly_window_slides(monkeypatch) -> None:
    monkeypatch.setenv("BUYSMARTER_AUTO_LEARN_PER_HOUR", "1")
    await mark_learning_started("чайник")
    assert not await _ok()

    auto_learn._starts[0] -= 3601  # the only start is now an hour old

    assert await _ok()


async def test_a_failure_mark_replaces_the_learn_in_progress_mark() -> None:
    await mark_learning_started(CATEGORY)
    await mark_learn_failed(CATEGORY)
    stored = await load_knowledge(CATEGORY) or {}

    assert LEARN_FAILED_KEY in stored
    assert LEARNING_STARTED_KEY not in stored
    assert not await _ok()


async def test_a_finished_learn_leaves_no_in_progress_mark(monkeypatch) -> None:
    learned: list[str] = []

    async def fake_learn(category: str):
        learned.append(category)
        assert LEARNING_STARTED_KEY in (await load_knowledge(category) or {})

    monkeypatch.setattr(auto_learn, "learn_category", fake_learn)

    assert await run_auto_learn(CATEGORY) is True
    stored = await load_knowledge(CATEGORY) or {}
    assert learned == [CATEGORY]
    assert LEARNING_STARTED_KEY not in stored and LEARN_FAILED_KEY not in stored


async def test_a_failed_learn_is_marked_and_logged(monkeypatch, caplog) -> None:
    async def boom(category: str):
        raise RuntimeError("web search down")

    monkeypatch.setattr(auto_learn, "learn_category", boom)

    assert await run_auto_learn(CATEGORY) is False
    stored = await load_knowledge(CATEGORY) or {}
    assert LEARN_FAILED_KEY in stored and LEARNING_STARTED_KEY not in stored
    assert any(CATEGORY in r.getMessage() and r.exc_info for r in caplog.records)
