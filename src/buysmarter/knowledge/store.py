"""Per-category knowledge store: BrandBook + ModelBook, DB-primary + file fallback.

Category knowledge (brand-trust tiers, product-identity models) is persisted per
category so any category — not just the hand-seeded irrigators — can accrue an
LLM-generated ``BrandBook`` + ``ModelBook`` (docs/PLAN.md фаза 4). Storage mirrors
``memory/prefs.py``: the Postgres ``CategoryKnowledge.schema_json`` column is
primary, and when the DB is unreachable the same functions transparently fall
back to a local JSON file ``~/.config/buysmarter/knowledge/<category-slug>.json``.
Override the directory with ``$BUYSMARTER_KNOWLEDGE_DIR`` (tests point it at a tmp
path).

Resolution (``resolve_books`` / ``get_brand_book`` / ``get_model_book``) prefers a
stored book, then falls back to the static seed (irrigators keep working offline
with no DB and no LLM), then ``None``.
"""

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.brands import get_brand_book as _seed_brand_book
from buysmarter.knowledge.ingredients import IngredientBook
from buysmarter.knowledge.models_book import ModelBook, ModelEntry
from buysmarter.knowledge.models_book import get_model_book as _seed_model_book
from buysmarter.llm.adapter import LlmConfigError, LlmError, generate
from buysmarter.llm.jev import system_one

logger = logging.getLogger(__name__)

# Matching a parsed category against stored knowledge slugs is a trivial
# classification task (pick the covering entry from a short list): Jev Choice
# first; the OmniRoute free-stack LLM (what the old "haiku" alias resolved to)
# is the fallback when Jev fails or is unsure.
MATCH_CATEGORY_MODEL = "free-stack"
# Jev answers below this confidence escalate to the LLM fallback.
# ponytail: tuned on 13 hand-labelled queries (wrong answer 0.51, right ones >= 0.73);
# retune after 50 real runs or at the first miss seen in one.
MATCH_MIN_CONFIDENCE = 0.7
# Choice option meaning "no stored category applies"; Jev caps a Choice at 255 options.
_NO_MATCH = "none of these"
_MAX_CHOICE_OPTIONS = 255

_DEFAULT_DIR = Path.home() / ".config" / "buysmarter" / "knowledge"

_BRAND_KEY = "brand_book"
_MODEL_KEY = "model_book"
_INGREDIENT_KEY = "ingredient_book"
_CATEGORY_TYPE_KEY = "category_type"
_GENERATED_BY = "generated_by"
_UPDATED_AT = "updated_at"

# Category type (docs/PLAN.md "Composition-driven категории"): a spec-driven
# category ranks on specs+brand (irrigators, phones); a composition-driven one
# (cosmetics/pharma/supplements) ranks on the active substance too.
SPEC_DRIVEN = "spec_driven"
COMPOSITION_DRIVEN = "composition_driven"


def _slug(category: str) -> str:
    """Filesystem-safe slug for a category (keeps Unicode letters, e.g. Cyrillic)."""
    text = re.sub(r"[\s/]+", "-", category.strip().lower())
    return re.sub(r"[^\w-]", "", text, flags=re.UNICODE) or "category"


def _knowledge_dir() -> Path:
    """Directory of the JSON fallback store (``$BUYSMARTER_KNOWLEDGE_DIR`` overrides)."""
    override = os.environ.get("BUYSMARTER_KNOWLEDGE_DIR")
    return Path(override).expanduser() if override else _DEFAULT_DIR


def _file(category: str) -> Path:
    return _knowledge_dir() / f"{_slug(category)}.json"


def _read_file(category: str) -> dict[str, Any] | None:
    path = _file(category)
    if not path.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Knowledge file unreadable (%r); treating as missing", exc)
        return None


def _write_file(category: str, payload: dict[str, Any]) -> None:
    path = _file(category)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def load_knowledge(category: str) -> dict[str, Any] | None:
    """Return the raw ``schema_json`` for a category; DB-primary, file fallback.

    Keyed by the category *slug* (not the raw string) in both stores, so spacing
    / case / hyphen variants of the same category ("робот пылесос" vs
    "робот-пылесос", as produced by query parsing vs the learn-category argument)
    resolve to the same knowledge.
    """
    key = _slug(category)
    try:
        async with async_session() as session:
            return await repo.get_category_knowledge(session, key)
    except Exception as exc:  # noqa: BLE001 - DB is optional, fall back to file
        logger.warning("Knowledge DB unavailable (%r); reading JSON fallback", exc)
        return _read_file(category)


async def save_knowledge(
    category: str,
    brand_book: BrandBook,
    model_book: ModelBook,
    generated_by: str,
    *,
    category_type: str | None = None,
    ingredient_book: IngredientBook | None = None,
) -> None:
    """Persist the books for a category (DB-primary; JSON fallback when DB is down).

    ``category_type`` and ``ingredient_book`` (composition-driven categories) are
    stored in the same payload. When either is left ``None`` the existing stored
    value is preserved — so best-effort saves that only touch the model book
    (e.g. learned OLX aliases) never wipe a category's ingredient knowledge.
    """
    existing = await load_knowledge(category) or {}
    if ingredient_book is None and existing.get(_INGREDIENT_KEY):
        ingredient_book = IngredientBook.model_validate(existing[_INGREDIENT_KEY])
    payload = {
        _BRAND_KEY: brand_book.model_dump(mode="json"),
        _MODEL_KEY: model_book.model_dump(mode="json"),
        _CATEGORY_TYPE_KEY: category_type or existing.get(_CATEGORY_TYPE_KEY, SPEC_DRIVEN),
        _GENERATED_BY: generated_by,
        _UPDATED_AT: datetime.now(UTC).isoformat(),
    }
    if ingredient_book is not None:
        payload[_INGREDIENT_KEY] = ingredient_book.model_dump(mode="json")
    try:
        async with async_session() as session:
            await repo.upsert_category_knowledge(session, _slug(category), payload)
            return
    except Exception as exc:  # noqa: BLE001 - DB is optional, fall back to file
        logger.warning("Knowledge DB unavailable (%r); writing JSON fallback", exc)
    _write_file(category, payload)


async def get_stored_books(category: str) -> tuple[BrandBook | None, ModelBook | None]:
    """Parse the stored books for a category (no seed fallback), or ``(None, None)``."""
    raw = await load_knowledge(category)
    if not raw:
        return None, None
    brand = BrandBook.model_validate(raw[_BRAND_KEY]) if raw.get(_BRAND_KEY) else None
    model = ModelBook.model_validate(raw[_MODEL_KEY]) if raw.get(_MODEL_KEY) else None
    return brand, model


async def resolve_composition(
    category: str, *, use_llm: bool = True
) -> tuple[str, IngredientBook | None]:
    """Resolve ``(category_type, ingredient_book)`` for a category.

    Mirrors ``resolve_books``' related-category fallback: an exact stored slug
    wins; otherwise a broader/related stored category (substring, then one cached
    LLM call — the same call ``resolve_books`` makes, so it hits cache) supplies
    the type/ingredients. Defaults to ``(SPEC_DRIVEN, None)`` when nothing is
    stored, so unknown categories keep the plain spec-driven ranking.
    """
    raw = await load_knowledge(category)
    if raw is None:
        matched = await _resolve_category_slug(_slug(category), use_llm)
        if matched is not None:
            raw = await load_knowledge(matched)
    if raw is None:
        return SPEC_DRIVEN, None
    category_type = raw.get(_CATEGORY_TYPE_KEY, SPEC_DRIVEN)
    ingredient_book = (
        IngredientBook.model_validate(raw[_INGREDIENT_KEY]) if raw.get(_INGREDIENT_KEY) else None
    )
    return category_type, ingredient_book


async def _list_stored_categories() -> list[str]:
    """Every stored category slug, DB-primary with a file-listing fallback."""
    try:
        async with async_session() as session:
            return await repo.list_category_slugs(session)
    except Exception as exc:  # noqa: BLE001 - DB is optional, fall back to files
        logger.warning("Knowledge DB unavailable (%r); listing JSON fallback", exc)
        knowledge_dir = _knowledge_dir()
        if not knowledge_dir.exists():
            return []
        return [p.stem for p in knowledge_dir.glob("*.json")]


def _substring_match(slug: str, candidates: list[str]) -> str | None:
    """Trivial no-LLM fallback: a stored slug that contains, or is contained by, ``slug``."""
    return next((c for c in candidates if slug in c or c in slug), None)


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
    """Jev Choice first; the LLM when Jev fails or answers below ``MATCH_MIN_CONFIDENCE``."""
    try:
        matched, confident = await _match_category_jev(category, candidates)
    except (LlmError, LlmConfigError) as exc:
        logger.warning("category-match Jev call failed (%r); falling back to the LLM", exc)
        return await _match_category_llm(category, candidates)
    if confident:
        return matched
    logger.info("category-match Jev unsure for %r; asking the LLM", category)
    return await _match_category_llm(category, candidates)


async def _resolve_category_slug(slug: str, use_llm: bool) -> str | None:
    """Find a stored category slug covering ``slug``: substring first, then LLM."""
    candidates = [c for c in await _list_stored_categories() if c != slug]
    if not candidates:
        return None
    matched = _substring_match(slug, candidates)
    if matched is not None:
        return matched
    return await _match_category(slug, candidates) if use_llm else None


async def resolve_books(
    category: str, *, use_llm: bool = True
) -> tuple[BrandBook | None, ModelBook | None]:
    """Resolve (brand, model) books: stored first, then static seed, then ``None``.

    When the exact category slug has no stored knowledge, this falls back to
    matching against other stored categories (substring, then one cached LLM
    call) so a narrower/related parsed category (e.g. "солнцезащитный крем")
    finds a broader stored book (e.g. "косметика") instead of surfacing "-"
    tiers for everything.
    """
    brand, model = await get_stored_books(category)
    if brand is None and model is None:
        matched = await _resolve_category_slug(_slug(category), use_llm)
        if matched is not None:
            logger.info("knowledge matched: %s -> %s", category, matched)
            brand, model = await get_stored_books(matched)
    return brand or _seed_brand_book(category), model or _seed_model_book(category)


async def get_brand_book(category: str) -> BrandBook | None:
    """BrandBook for a category: stored first, static seed fallback, else ``None``."""
    return (await resolve_books(category))[0]


async def get_model_book(category: str) -> ModelBook | None:
    """ModelBook for a category: stored first, static seed fallback, else ``None``."""
    return (await resolve_books(category))[1]


def merge_brand_books(base: BrandBook, overlay: BrandBook) -> BrandBook:
    """Merge two BrandBooks; ``overlay`` (the user's file) wins on conflicts."""
    return BrandBook(
        tiers={**base.tiers, **overlay.tiers},
        aliases={**base.aliases, **overlay.aliases},
    )


def merge_model_books(base: ModelBook, overlay: ModelBook) -> ModelBook:
    """Merge two ModelBooks by canonical name; ``overlay`` entries win on conflicts."""
    by_canonical: dict[str, ModelEntry] = {e.canonical: e for e in base.entries}
    for entry in overlay.entries:
        by_canonical[entry.canonical] = entry
    return ModelBook(entries=list(by_canonical.values()))
