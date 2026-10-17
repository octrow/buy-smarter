"""LLM canonicalization wiring for used-OLX matching (stubbed generate).

Covers: map-to-existing learns + persists an alias (file fallback store),
new-canonical suggestions are display-only, null/fail-open leave the book
untouched, and nothing-unresolved skips the LLM call entirely.
"""

import pytest

from buysmarter.knowledge.models_book import ModelBook, ModelEntry
from buysmarter.knowledge.store import get_stored_books
from buysmarter.llm import canonicalize as canon
from buysmarter.llm.canonicalize import CanonicalizeResult, CanonicalMapping
from buysmarter.ranking.models import Candidate
from buysmarter.services.matching import canonicalize_used_titles, match_used_listings


@pytest.fixture(autouse=True)
def _tmp_knowledge_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BUYSMARTER_KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr("buysmarter.knowledge.store.async_session", _no_db)


def _no_db(*args, **kwargs):
    raise RuntimeError("DB disabled in test — use file fallback")


def _used(pid: str, title: str, price: int) -> Candidate:
    return Candidate(source="olx", source_product_id=pid, title=title, brand="Xiaomi", price=price)


def _book() -> ModelBook:
    return ModelBook(
        entries=[ModelEntry(canonical="Xiaomi F300", brand="Xiaomi", codes=["MEO703", "F300"])]
    )


async def test_skips_llm_when_nothing_unresolved(monkeypatch) -> None:
    called = False

    async def _fake_generate(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("should not be called")

    monkeypatch.setattr(canon, "generate", _fake_generate)
    book = _book()
    used = [_used("u1", "Xiaomi F300 ирригатор", 9000)]  # resolves via code already

    result_book, labels = await canonicalize_used_titles("ирригатор", None, book, used, True)

    assert called is False
    assert labels == {}
    assert result_book is book


async def test_skips_when_use_llm_false() -> None:
    book = _book()
    used = [_used("u1", "Xiaomi ирригатор MEO701", 8000)]

    result_book, labels = await canonicalize_used_titles("ирригатор", None, book, used, False)

    assert result_book is book
    assert labels == {}


async def test_maps_to_existing_learns_alias_and_persists(monkeypatch) -> None:
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return CanonicalizeResult(
            mappings=[CanonicalMapping(index=0, canonical="Xiaomi F300", is_new=False)]
        )

    monkeypatch.setattr(canon, "generate", _fake_generate)
    book = _book()
    title = "Ирригатор xiaomi мийа флоссер б/у"  # no F300/MEO703 code token — unresolved
    used = [_used("u1", title, 8000)]

    updated_book, labels = await canonicalize_used_titles("ирригатор", None, book, used, True)

    assert labels == {}
    entry = next(e for e in updated_book.entries if e.canonical == "Xiaomi F300")
    assert title in entry.aliases
    assert updated_book.resolve(title) == "Xiaomi F300"  # next run resolves without LLM

    stored_brand, stored_model = await get_stored_books("ирригатор")
    assert stored_brand is not None
    stored_entry = next(e for e in stored_model.entries if e.canonical == "Xiaomi F300")
    assert title in stored_entry.aliases


async def test_new_canonical_is_display_only_not_a_match(monkeypatch) -> None:
    new_label = "Xiaomi Water Flosser 1 (MEO701)"

    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return CanonicalizeResult(
            mappings=[CanonicalMapping(index=0, canonical=new_label, is_new=True)]
        )

    monkeypatch.setattr(canon, "generate", _fake_generate)
    book = _book()
    title = "Xiaomi ирригатор Mijia Electric Flusher MEO701"
    used_cand = _used("u1", title, 8000)

    updated_book, labels = await canonicalize_used_titles(
        "ирригатор", None, book, [used_cand], True
    )

    assert labels == {title: new_label}
    # the new suggestion must NOT become an alias of the existing F300 entry
    entry = next(e for e in updated_book.entries if e.canonical == "Xiaomi F300")
    assert title not in entry.aliases
    assert updated_book.resolve(title) is None

    new_ref = Candidate(
        source="kaspi",
        source_product_id="k1",
        title="Xiaomi F300 ирригатор",
        brand="Xiaomi",
        price=30000,
    )
    listings = match_used_listings([new_ref], [used_cand], None, updated_book, labels)
    assert len(listings) == 1
    # matched via brand+overlap only; the display label is its OWN suggestion,
    # not mislabeled as the matched F300 ref.
    assert listings[0].canonical == new_label
    assert listings[0].matched_title == new_ref.title


async def test_null_mapping_leaves_book_unchanged(monkeypatch) -> None:
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return CanonicalizeResult(mappings=[CanonicalMapping(index=0, canonical=None)])

    monkeypatch.setattr(canon, "generate", _fake_generate)
    book = _book()
    used = [_used("u1", "непонятная штука без опознавательных знаков", 8000)]

    updated_book, labels = await canonicalize_used_titles("ирригатор", None, book, used, True)

    assert labels == {}
    assert updated_book is book


async def test_fail_open_on_llm_error(monkeypatch) -> None:
    async def _fake_generate(*args, **kwargs):
        raise canon.LlmError("boom")

    monkeypatch.setattr(canon, "generate", _fake_generate)
    book = _book()
    used = [_used("u1", "Xiaomi ирригатор MEO701", 8000)]

    updated_book, labels = await canonicalize_used_titles("ирригатор", None, book, used, True)

    assert labels == {}
    assert updated_book is book
