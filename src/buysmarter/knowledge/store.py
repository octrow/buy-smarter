"""Per-category knowledge store: BrandBook + ModelBook in the local store.

Category knowledge (brand-trust tiers, product-identity models) is persisted per
category so any category — not just the hand-seeded irrigators — can accrue an
LLM-generated ``BrandBook`` + ``ModelBook`` (docs/PLAN.md фаза 4), kept in the
``CategoryKnowledge.schema_json`` column. Reads treat an unavailable store as "nothing
learned"; writes are best-effort and log a warning. Earlier versions kept one JSON file per
category under ``$BUYSMARTER_KNOWLEDGE_DIR``; ``import_legacy_dir`` carries them over once.

Resolution (``resolve_profile``) reads a record as a category profile
(``knowledge.profile``): the exact slug, then a profile listing it as a synonym, then a
Jev/LLM pick among stored profiles; the parent fills missing fields, and missing books
fall back to the static seed (irrigators keep working offline with no DB and no LLM).
"""

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.brands import get_brand_book as _seed_brand_book
from buysmarter.knowledge.ingredients import IngredientBook
from buysmarter.knowledge.models_book import ModelBook, ModelEntry
from buysmarter.knowledge.models_book import get_model_book as _seed_model_book
from buysmarter.knowledge.profile import (
    BRAND_KEY,
    COMPOSITION_DRIVEN,
    HEAL_CHECKED_KEY,
    INGREDIENT_KEY,
    MODEL_KEY,
    PARENT_KEY,
    SPEC_DRIVEN,
    SYNONYMS_KEY,
    CategoryProfile,
    ResolvedProfile,
    composition_keys,
    profile_from_record,
    resolve_fields,
)
from buysmarter.llm.adapter import LlmConfigError, LlmError, generate
from buysmarter.llm.jev import system_one
from buysmarter.local_store import set_aside
from buysmarter.ranking.audience import bare_product_type

logger = logging.getLogger(__name__)

# Matching a parsed category against stored knowledge slugs is a trivial
# classification task (pick the covering entry from a short list): Jev Choice
# first; the OmniRoute free-stack LLM (what the old "haiku" alias resolved to)
# is the fallback when Jev fails.
MATCH_CATEGORY_MODEL = "free-stack"
# Jev answers below this confidence mean no related category.
# ponytail: tuned on 13 hand-labelled queries (wrong answer 0.51, right ones >= 0.73);
# retune after 50 real runs or at the first miss seen in one.
MATCH_MIN_CONFIDENCE = 0.7
# Choice option meaning "no stored category applies"; Jev caps a Choice at 255 options.
_NO_MATCH = "none of these"
_MAX_CHOICE_OPTIONS = 255

_DEFAULT_DIR = Path.home() / ".config" / "buysmarter" / "knowledge"

_GENERATED_BY = "generated_by"
_UPDATED_BY = "updated_by"
_UPDATED_AT = "updated_at"


def _slug(category: str) -> str:
    """Filesystem-safe slug for a category (keeps Unicode letters, e.g. Cyrillic)."""
    text = re.sub(r"[\s/]+", "-", category.strip().lower())
    return re.sub(r"[^\w-]", "", text, flags=re.UNICODE) or "category"


def _legacy_dir() -> Path:
    """The pre-store JSON directory (``$BUYSMARTER_KNOWLEDGE_DIR`` overrides)."""
    override = os.environ.get("BUYSMARTER_KNOWLEDGE_DIR")
    return Path(override).expanduser() if override else _DEFAULT_DIR


async def load_knowledge(category: str) -> dict[str, Any] | None:
    """Return the raw ``schema_json`` for a category, or ``None`` (also when the store is off).

    Keyed by the category *slug* (not the raw string) in both stores, so spacing
    / case / hyphen variants of the same category ("робот пылесос" vs
    "робот-пылесос", as produced by query parsing vs the learn-category argument)
    resolve to the same knowledge.
    """
    key = _slug(category)
    try:
        async with async_session() as session:
            return await repo.get_category_knowledge(session, key)
    except Exception as exc:  # noqa: BLE001 - the store is optional at read time
        logger.warning("Knowledge store unavailable (%r); nothing learned applies", exc)
        return None


async def save_knowledge(
    category: str,
    brand_book: BrandBook | None = None,
    model_book: ModelBook | None = None,
    generated_by: str = "",
    *,
    category_type: str | None = None,
    ingredient_book: IngredientBook | None = None,
) -> None:
    """Merge the given books into a category's stored record (best-effort: failures are logged).

    Only the fields passed are written; every other stored key (the other book, the
    ingredient book, ``parent``, ``synonyms``, ``capabilities``...) is kept, so a save
    that only touches the model book (learned OLX aliases) never wipes the rest.
    ``generated_by`` names the writer: it becomes the record's ``generated_by`` only on
    a new record, and always its ``updated_by``.
    """
    updates: dict[str, Any] = {}
    if brand_book is not None:
        updates[BRAND_KEY] = brand_book.model_dump(mode="json")
    if model_book is not None:
        updates[MODEL_KEY] = model_book.model_dump(mode="json")
    if ingredient_book is not None:
        updates[INGREDIENT_KEY] = ingredient_book.model_dump(mode="json")
    if category_type is not None:
        stored = await load_knowledge(category) or {}
        if ingredient_book is None and stored.get(INGREDIENT_KEY):
            ingredient_book = IngredientBook.model_validate(stored[INGREDIENT_KEY])
        updates.update(composition_keys(stored, category_type, ingredient_book))
    await update_knowledge(category, updates, generated_by)


async def update_knowledge(
    category: str, updates: dict[str, Any], writer: str, *, drop: tuple[str, ...] = ()
) -> None:
    """Merge raw ``updates`` into the stored record, remove the ``drop`` keys, stamp the writer.

    ponytail: read-modify-write without a lock, so two saves to one slug at once (an API
    worker and a heal) can lose a key, as the rebuild-on-save before it could; move the
    merge into one UPDATE transaction if concurrent writers ever show up in the logs.
    """
    existing = await load_knowledge(category) or {}
    payload = {k: v for k, v in existing.items() if k not in drop}
    payload.update(updates)
    payload[_GENERATED_BY] = existing.get(_GENERATED_BY) or writer
    payload[_UPDATED_BY] = writer
    payload[_UPDATED_AT] = datetime.now(UTC).isoformat()
    try:
        async with async_session() as session:
            await repo.upsert_category_knowledge(session, _slug(category), payload)
    except Exception as exc:  # noqa: BLE001 - learned knowledge is best-effort
        logger.warning("Knowledge store unavailable (%r); %s not saved", exc, category)


async def get_stored_books(category: str) -> tuple[BrandBook | None, ModelBook | None]:
    """Parse the stored books for a category (no seed fallback), or ``(None, None)``."""
    raw = await load_knowledge(category)
    if not raw:
        return None, None
    brand = BrandBook.model_validate(raw[BRAND_KEY]) if raw.get(BRAND_KEY) else None
    model = ModelBook.model_validate(raw[MODEL_KEY]) if raw.get(MODEL_KEY) else None
    return brand, model


async def load_profile(category: str) -> CategoryProfile | None:
    """The category's own stored profile, no inheritance and no lookup (``None`` if none)."""
    raw = await load_knowledge(category)
    return profile_from_record(_slug(category), raw) if raw is not None else None


async def resolve_profile(
    category: str, *, use_llm: bool = True, search_phrase: str | None = None
) -> ResolvedProfile:
    """The profile a query uses: exact slug, synonyms, the old slug, a Jev/LLM pick, none.

    ``category`` is the product type; ``search_phrase`` (when it differs) may name an old
    record keyed by the whole phrase ("мужские-кроссовки-42"), see ``_old_slug``. No
    substring matching (``"чай"`` must not find ``"чайник"``). The found profile's
    parent fills the fields it lacks (one step). Books nothing stored provides fall back
    to the static seed, owned by the query's slug.

    May write: an old copy left by the pre-profile alias save is healed once (the record,
    and the aliases moved to its source; see ``_heal``); an old slug is moved or listed
    as a synonym.
    """
    slug = _slug(category)
    own = await load_profile(category)
    if own is not None and _heal_candidate(own):
        own = await _heal(own, use_llm)
    if own is None:
        own = await _by_synonym(slug)
    old = _slug(search_phrase) if search_phrase and slug else slug
    if old != slug:
        own = await _old_slug(own, slug, old, search_phrase or "")
    if own is None and use_llm:
        matched = await _match_related(slug)
        if matched is not None:
            logger.info("knowledge matched: %s -> %s", category, matched)
            own = await load_profile(matched)
    parent = await load_profile(own.parent) if own is not None and own.parent else None
    resolved = resolve_fields(slug, own, parent)
    return _with_seed_books(resolved, category)


# --- Healing records the pre-phase-1 alias save copied (decision 8) ---------------
# _persist_learned_aliases used to save a borrowed brand book and the model book with its
# aliases under the query's category, typed spec-driven: from then on that copy shadowed
# the composition profile it came from ("крем с адапаленом" stopped using "косметика").

_HEAL_WRITER = "knowledge-heal"


def _heal_candidate(own: CategoryProfile) -> bool:
    """An old-format record with a brand book, no capabilities and no parent, not yet checked."""
    return (
        own.capabilities is None
        and own.brand_book is not None
        and own.parent is None
        and not own.heal_checked
    )


def _is_copy_of(own: CategoryProfile, source: CategoryProfile) -> bool:
    """The record's brand book is exactly a composition profile's: the copy, not a learned book."""
    return (
        source.ingredient_book is not None
        and source.brand_book is not None
        and own.brand_book is not None
        and own.brand_book.model_dump() == source.brand_book.model_dump()
    )


def _merge_aliases(base: ModelBook, extra: ModelBook) -> ModelBook:
    """``base`` plus the aliases and models only ``extra`` has; nothing of ``base`` is lost."""
    by_canonical = {e.canonical: e for e in base.entries}
    for entry in extra.entries:
        have = by_canonical.get(entry.canonical)
        aliases = list(dict.fromkeys([*have.aliases, *entry.aliases])) if have else None
        by_canonical[entry.canonical] = (
            have.model_copy(update={"aliases": aliases}) if have else entry
        )
    return ModelBook(entries=list(by_canonical.values()))


async def _heal(own: CategoryProfile, use_llm: bool) -> CategoryProfile:
    """Turn a copy into a child of the profile it was copied from; mark the record checked.

    The copied brand book is dropped, the learned aliases move to the source's model book
    and the record's own model book is dropped. Any outcome of a full check (one that
    could ask Jev) marks the record, so the check never runs again.
    """
    related = await _find_related(own.slug, use_llm)
    source = await load_profile(related) if related is not None else None
    if source is None or not _is_copy_of(own, source):
        if related is not None or use_llm:
            await update_knowledge(own.slug, {HEAL_CHECKED_KEY: True}, _HEAL_WRITER)
        return own
    if own.model_book is not None:
        merged = _merge_aliases(source.model_book or ModelBook(), own.model_book)
        await save_knowledge(source.slug, model_book=merged, generated_by=_HEAL_WRITER)
    await update_knowledge(
        own.slug,
        {PARENT_KEY: source.slug, HEAL_CHECKED_KEY: True},
        _HEAL_WRITER,
        drop=(BRAND_KEY, MODEL_KEY),
    )
    logger.info("knowledge healed: %s -> parent %s", own.slug, source.slug)
    return await load_profile(own.slug) or own


# --- The profile key is the product type (phase 2, decision 11) ---------------------
# Records used to be keyed by the whole parsed category ("мужские-кроссовки-42"). Such a
# record moves under the product type when the two differ only by audience or size words;
# a narrower record ("шампунь-от-перхоти") may carry its own composition and is never merged.

_RENAME_WRITER = "knowledge-rename"


def _variant_of(search_phrase: str, slug: str) -> bool:
    """The phrase is the product type plus audience or size words only."""
    return _slug(bare_product_type(search_phrase)) == slug


async def _old_slug(
    own: CategoryProfile | None, slug: str, old: str, search_phrase: str
) -> CategoryProfile | None:
    """Use, move or list the record keyed by the search phrase; returns the profile to use."""
    raw = await load_knowledge(old)
    if raw is None:
        return own
    if not _variant_of(search_phrase, slug):
        return own if own is not None else profile_from_record(old, raw)
    if own is None:
        await _rename(old, slug)
        return await load_profile(slug)
    if old != own.slug and old not in own.synonyms:
        await update_knowledge(own.slug, {SYNONYMS_KEY: [*own.synonyms, old]}, _RENAME_WRITER)
        logger.info("knowledge synonym: %s listed under %s", old, own.slug)
        return await load_profile(own.slug) or own
    return own


async def _rename(old: str, new: str) -> None:
    """Move ``old`` to ``new`` once (one UPDATE), list it as a synonym, re-point children.

    A lookup running at the same time finds the record already moved (no row, or ``new``
    taken): it only reads ``new`` back.
    """
    try:
        async with async_session() as session:
            moved = await repo.rename_category_knowledge(session, old, new)
    except IntegrityError:
        moved = False
    except Exception as exc:  # noqa: BLE001 - the store is optional
        logger.warning("Knowledge store unavailable (%r); %s not renamed", exc, old)
        return
    if not moved:
        return
    stored = await load_knowledge(new) or {}
    synonyms = [s for s in stored.get(SYNONYMS_KEY) or [] if s != old]
    await update_knowledge(new, {SYNONYMS_KEY: [*synonyms, old]}, _RENAME_WRITER)
    for child, raw in (await _stored_records()).items():
        if raw.get(PARENT_KEY) == old:
            await update_knowledge(child, {PARENT_KEY: new}, _RENAME_WRITER)
    logger.info("knowledge renamed: %s -> %s", old, new)


def _with_seed_books(resolved: ResolvedProfile, category: str) -> ResolvedProfile:
    """Fill a missing book from the static seed (irrigators), owned by the query's slug."""
    updates: dict[str, Any] = {}
    if resolved.brand_book is None and (brand := _seed_brand_book(category)) is not None:
        updates.update(brand_book=brand, brand_book_owner=resolved.slug)
    if resolved.model_book is None and (model := _seed_model_book(category)) is not None:
        updates.update(model_book=model, model_book_owner=resolved.slug)
    return resolved.model_copy(update=updates) if updates else resolved


async def resolve_composition(
    category: str, *, use_llm: bool = True
) -> tuple[str, IngredientBook | None]:
    """``(category_type, ingredient_book)`` of the resolved profile (legacy reading)."""
    ingredient_book = (await resolve_profile(category, use_llm=use_llm)).ingredient_book
    return (COMPOSITION_DRIVEN if ingredient_book is not None else SPEC_DRIVEN), ingredient_book


async def _stored_records() -> dict[str, dict[str, Any]]:
    """Every stored record by slug (``{}`` when the store is off)."""
    try:
        async with async_session() as session:
            return await repo.list_category_knowledge(session)
    except Exception as exc:  # noqa: BLE001 - the store is optional at read time
        logger.warning("Knowledge store unavailable (%r); no stored categories", exc)
        return {}


async def import_legacy_dir() -> None:
    """Import categories the store lacks from the old JSON files once, then rename the dir.

    The directory becomes ``*.imported``; an unreadable file is skipped with a warning. Re-running
    is safe: only missing slugs are written.
    """
    legacy = _legacy_dir()
    if not legacy.is_dir():
        return
    async with async_session() as session:
        stored = set(await repo.list_category_slugs(session))
        for path in sorted(legacy.glob("*.json")):
            if path.stem in stored:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                payload, reason = None, repr(exc)
            else:
                reason = "not a JSON object"
            if not isinstance(payload, dict):
                logger.warning("Legacy knowledge file %s unreadable (%s); skipped", path, reason)
                continue
            await repo.upsert_category_knowledge(session, path.stem, payload)
    set_aside(legacy, ".imported")


def _synonym_owner(slug: str, records: dict[str, dict[str, Any]]) -> str | None:
    """The stored slug that lists ``slug`` among its synonyms."""
    for owner, raw in records.items():
        if any(_slug(s) == slug for s in raw.get(SYNONYMS_KEY) or []):
            return owner
    return None


class _CategoryMatch(BaseModel):
    """LLM verdict: index (into the candidate list) of the covering category, or None."""

    index: int | None = None


def match_category_prompt(category: str, candidates: list[str]) -> str:
    """Build the batched "which stored category covers this one" prompt."""
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(candidates))
    return (
        f'A shopper searched for category "{category}". Below are category '
        "records already stored from prior sessions, each possibly broader or "
        'narrower than the query (e.g. a stored "косметика" covers a query '
        '"солнцезащитный крем", since sunscreen is a cosmetics sub-category). '
        "Which stored category, if any, covers the same products as the "
        'query category? Return JSON: {"index": <index>} using the numbers '
        'shown below, or {"index": null} if none of them apply.\n\n'
        f"{numbered}"
    )


def match_category_question(category: str, candidates: list[str]) -> dict[str, Any]:
    """One Jev Choice over the stored slugs plus an explicit no-match option."""
    criteria: dict[str, str | None] = {c: None for c in candidates}
    criteria[_NO_MATCH] = "No stored category covers the same products as the query"
    return {
        "type": "choice",
        "instructions": {
            "query_category": category,
            "question": "Which stored product category covers the same products as "
            '`query_category`? A broader category covers a narrower one (a stored "косметика" '
            'covers "солнцезащитный крем"). Categories may be Russian or English slugs.',
        },
        "criteria": criteria,
    }


async def _match_category_jev(category: str, candidates: list[str]) -> tuple[str | None, bool]:
    """Jev pick: ``(match_or_None, confident)``. Raises ``LlmError`` / ``LlmConfigError``."""
    if len(candidates) >= _MAX_CHOICE_OPTIONS:
        raise LlmConfigError(f"{len(candidates)} stored categories exceed a Jev Choice")
    answers = await system_one(
        {"query_category": category, "stored_categories": candidates},
        {"match": match_category_question(category, candidates)},
    )
    try:
        choice, confidence = answers["match"]["choice"], answers["match"]["confidence"]
    except (KeyError, TypeError) as exc:
        raise LlmError(f"malformed Jev choice answer: {exc!r}") from exc
    matched = choice if choice in candidates else None
    return matched, confidence >= MATCH_MIN_CONFIDENCE


async def _match_category_llm(category: str, candidates: list[str]) -> str | None:
    """One batched, cached LLM call picking the covering stored category, or None."""
    try:
        result = await generate(
            match_category_prompt(category, candidates), _CategoryMatch, model=MATCH_CATEGORY_MODEL
        )
    except (LlmError, LlmConfigError) as exc:
        logger.warning("category-match LLM call failed (%r); no knowledge match", exc)
        return None
    index = cast(_CategoryMatch, result).index  # generate() returns the model it was given
    if index is None or index < 0 or index >= len(candidates):
        return None
    return candidates[index]


async def _match_category(category: str, candidates: list[str]) -> str | None:
    """Jev Choice; the LLM only when Jev fails. Below ``MATCH_MIN_CONFIDENCE`` means no match.

    The LLM once asked on unsure answers mapped "чехол для iPhone 15" to "смартфон" and found
    nothing for "кроссовки" or "крем", 40-60 s each.
    """
    try:
        matched, confident = await _match_category_jev(category, candidates)
    except (LlmError, LlmConfigError) as exc:
        logger.warning("category-match Jev call failed (%r); falling back to the LLM", exc)
        return await _match_category_llm(category, candidates)
    if confident:
        return matched
    logger.info("category-match Jev unsure for %r; no related category", category)
    return None


async def _other_records(slug: str) -> dict[str, dict[str, Any]]:
    return {s: raw for s, raw in (await _stored_records()).items() if s != slug}


async def _by_synonym(slug: str) -> CategoryProfile | None:
    """The stored profile listing ``slug`` among its synonyms."""
    owner = _synonym_owner(slug, await _other_records(slug))
    if owner is None:
        return None
    logger.info("knowledge matched: %s -> %s", slug, owner)
    return await load_profile(owner)


async def _match_related(slug: str) -> str | None:
    """A Jev/LLM pick among the other stored profiles."""
    records = await _other_records(slug)
    return await _match_category(slug, list(records)) if records else None


async def _find_related(slug: str, use_llm: bool) -> str | None:
    """A stored profile covering ``slug``: one listing ``slug`` as a synonym, else a Jev/LLM pick.

    No substring match: it found "чайник" for "чай" (logic-map finding #15).
    """
    records = await _other_records(slug)
    if not records:
        return None
    owner = _synonym_owner(slug, records)
    if owner is not None or not use_llm:
        return owner
    return await _match_category(slug, list(records))


async def resolve_books(
    category: str, *, use_llm: bool = True
) -> tuple[BrandBook | None, ModelBook | None]:
    """(brand, model) books of the resolved profile: stored, inherited, related or seed."""
    resolved = await resolve_profile(category, use_llm=use_llm)
    return resolved.brand_book, resolved.model_book


def merge_brand_books(base: BrandBook, overlay: BrandBook) -> BrandBook:
    """Merge two BrandBooks; ``overlay`` (the user's file) wins on conflicts."""
    return BrandBook(
        tiers={**base.tiers, **overlay.tiers},
        aliases={**base.aliases, **overlay.aliases},
        price_floors={**base.price_floors, **overlay.price_floors},
        official_sellers={**base.official_sellers, **overlay.official_sellers},
        official_stores={**base.official_stores, **overlay.official_stores},
    )


def merge_model_books(base: ModelBook, overlay: ModelBook) -> ModelBook:
    """Merge two ModelBooks by canonical name; ``overlay`` entries win on conflicts."""
    by_canonical: dict[str, ModelEntry] = {e.canonical: e for e in base.entries}
    for entry in overlay.entries:
        by_canonical[entry.canonical] = entry
    return ModelBook(entries=list(by_canonical.values()))
