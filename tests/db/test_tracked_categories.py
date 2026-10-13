"""Pure unit tests for the tracked-categories dedup/cap policy (no DB)."""

from buysmarter.db.repo import merge_tracked_categories


def test_new_category_prepended_most_recent_first():
    assert merge_tracked_categories(["a", "b"], "c") == ["c", "a", "b"]


def test_duplicate_moves_to_front_without_repeating():
    assert merge_tracked_categories(["a", "b", "c"], "b") == ["b", "a", "c"]


def test_capped_at_limit():
    existing = [f"cat{i}" for i in range(20)]
    merged = merge_tracked_categories(existing, "new", cap=20)
    assert len(merged) == 20
    assert merged[0] == "new"
    assert "cat19" not in merged  # oldest dropped when over the cap


def test_empty_existing_list():
    assert merge_tracked_categories([], "first") == ["first"]
