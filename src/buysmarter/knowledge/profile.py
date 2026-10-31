"""Category profile: one stored knowledge record read as books, capabilities and a parent.

A profile lives in the same ``category_knowledge.schema_json`` as before (ADR-0005,
docs/PLAN.md, 2026-10-22-capabilities-phase1 decision 1). New keys: ``capabilities``
(``{name: {enabled, params, set_by, user}}``), ``synonyms``, ``parent``, ``updated_by``,
``heal_checked``. Old records are read through an adapter: ``category_type ==
composition_driven`` becomes the composition capability with the ingredient book as its
parameter. Until phase 3 the old keys are written too (``composition_keys``), so an older
app version still reads the record.

Inheritance is one step: a field the profile does not set comes from its parent, and the
resolved profile names the owner of each book, so knowledge learned while using a book
is saved where the book lives. Capabilities are inherited by name (``combine``).

A record without books (only Jev's capabilities, a user's choice, learning marks) is no
profile of its own unless it names a parent (phase 3, decision 1): ``has_books``.
"""

from typing import Any, Literal, TypeGuard

from pydantic import BaseModel, Field, model_validator

from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.ingredients import IngredientBook
from buysmarter.knowledge.models_book import ModelBook

# Category type (legacy, still written): a spec-driven category ranks on specs + brand;
# a composition-driven one (cosmetics, pharmacy, supplements) on the active substance too.
SPEC_DRIVEN = "spec_driven"
COMPOSITION_DRIVEN = "composition_driven"
COMPOSITION = "composition"

BRAND_KEY = "brand_book"
MODEL_KEY = "model_book"
INGREDIENT_KEY = "ingredient_book"
CATEGORY_TYPE_KEY = "category_type"
CAPABILITIES_KEY = "capabilities"
SYNONYMS_KEY = "synonyms"
PARENT_KEY = "parent"
HEAL_CHECKED_KEY = "heal_checked"

# Who set a capability's automatic value, strongest first (ADR-0005 "Category profile");
# the user's own choice is a separate field and outranks them all.
SetBy = Literal["learned", "jev", "fallback"]
_RANK: dict[SetBy | None, int] = {None: -1, "fallback": 0, "jev": 1, "learned": 2}


class CapabilitySetting(BaseModel):
    """One capability in a profile: the automatic value, its parameters and the user's choice.

    ``enabled``/``params``/``set_by`` are the automatic value (``set_by=None``: there is
    none, the entry only holds the user's choice); ``user`` is the user's own switch
    (``None``: untouched). Resetting the choice keeps the learned parameters.
    """

    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    set_by: SetBy | None = "learned"
    user: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _old_user_choice(cls, data: Any) -> Any:
        """Phase 1 stored the user's choice as ``set_by="user"``."""
        if isinstance(data, dict) and data.get("set_by") == "user":
            return {**data, "user": data.get("enabled", True), "set_by": "learned"}
        return data

    @property
    def on(self) -> bool:
        """The effective switch: the user's choice when made, else the automatic value."""
        return self.enabled if self.user is None else self.user


def combine(base: CapabilitySetting | None, top: CapabilitySetting) -> CapabilitySetting:
    """``top`` over ``base``: the stronger automatic value (``top`` on a tie), and ``top``'s
    user choice, else ``base``'s. One rule for a child over its parent and for a record
    without books over the profile found for it."""
    if base is None:
        return top
    auto = top if _RANK[top.set_by] >= _RANK[base.set_by] else base
    user = top.user if top.user is not None else base.user
    return auto.model_copy(update={"user": user})


def combine_all(
    base: dict[str, CapabilitySetting], top: dict[str, CapabilitySetting]
) -> dict[str, CapabilitySetting]:
    """``combine`` by name: a capability ``top`` does not name stays as in ``base``."""
    return {**base, **{name: combine(base.get(name), s) for name, s in top.items()}}


def has_books(raw: dict[str, Any]) -> bool:
    """A stored record with a brand or a model book: a profile, not only capabilities."""
    return bool(raw.get(BRAND_KEY) or raw.get(MODEL_KEY))


class CategoryProfile(BaseModel):
    """One stored record, own fields only (``None`` == not set here, inherit)."""

    slug: str
    brand_book: BrandBook | None = None
    model_book: ModelBook | None = None
    capabilities: dict[str, CapabilitySetting] | None = None
    synonyms: list[str] = Field(default_factory=list)
    parent: str | None = None
    heal_checked: bool = False

    @property
    def ingredient_book(self) -> IngredientBook | None:
        """The composition capability's ingredient book, if this profile switches it on."""
        return composition_book(self.capabilities or {})

    @property
    def is_profile(self) -> bool:
        """Books, or a parent to take them from: a record the lookup may stop at."""
        return self.brand_book is not None or self.model_book is not None or bool(self.parent)


class ResolvedProfile(BaseModel):
    """What a query uses: each field after inheritance, and the slug that owns it.

    An owner is the query's own slug, its parent's, or the related profile the lookup
    found; ``found`` is the stored record the lookup settled on (the query's own, a
    synonym's owner or a related profile; ``None``: nothing). Books from the static seed are
    owned by the query's slug (nothing stored owns them, so learned aliases go under the
    query, as before phase 1).
    """

    slug: str
    brand_book: BrandBook | None = None
    model_book: ModelBook | None = None
    capabilities: dict[str, CapabilitySetting] = Field(default_factory=dict)
    brand_book_owner: str | None = None
    model_book_owner: str | None = None
    found: str | None = None

    @property
    def ingredient_book(self) -> IngredientBook | None:
        return composition_book(self.capabilities)

    def enabled(self, name: str) -> CapabilitySetting | None:
        """The capability's setting when the profile switches it on, else ``None``."""
        setting = self.capabilities.get(name)
        return setting if _switched_on(setting) else None


def _switched_on(setting: CapabilitySetting | None) -> TypeGuard[CapabilitySetting]:
    return setting is not None and setting.on


def composition_book(capabilities: dict[str, CapabilitySetting]) -> IngredientBook | None:
    """The ingredient book of an enabled composition capability.

    No book stored reads as off, as an old composition record without one always did; so does
    a book with no substances (a spec-driven category's learning stores one).
    """
    setting = capabilities.get(COMPOSITION)
    if not (_switched_on(setting) and (raw := setting.params.get(INGREDIENT_KEY))):
        return None
    book = IngredientBook.model_validate(raw)
    return book if book.substances else None


def stored_capabilities(raw: dict[str, Any]) -> dict[str, CapabilitySetting] | None:
    """Stored capabilities, or the adapter's reading of an old composition record."""
    if isinstance(raw.get(CAPABILITIES_KEY), dict):
        return {
            name: CapabilitySetting.model_validate(value)
            for name, value in raw[CAPABILITIES_KEY].items()
        }
    if raw.get(CATEGORY_TYPE_KEY) != COMPOSITION_DRIVEN:
        return None
    params = {INGREDIENT_KEY: raw[INGREDIENT_KEY]} if raw.get(INGREDIENT_KEY) else {}
    return {COMPOSITION: CapabilitySetting(params=params)}


def profile_from_record(slug: str, raw: dict[str, Any]) -> CategoryProfile:
    """Read one stored record (old or new format) as a profile."""
    return CategoryProfile(
        slug=slug,
        brand_book=BrandBook.model_validate(raw[BRAND_KEY]) if raw.get(BRAND_KEY) else None,
        model_book=ModelBook.model_validate(raw[MODEL_KEY]) if raw.get(MODEL_KEY) else None,
        capabilities=stored_capabilities(raw),
        synonyms=list(raw.get(SYNONYMS_KEY) or []),
        parent=raw.get(PARENT_KEY) or None,
        heal_checked=bool(raw.get(HEAL_CHECKED_KEY)),
    )


def merge_capabilities(
    stored: dict[str, CapabilitySetting], incoming: dict[str, Any], *, by_user: bool
) -> dict[str, Any]:
    """The stored capabilities after one writer's ``incoming`` ones, by name (raw dicts).

    An automatic writer changes a capability's automatic value only when its source is
    not weaker than the stored one (``combine``) and never touches ``user``; the user's
    writer (``by_user``) changes only ``user``. An entry left with neither is removed.
    """
    merged = dict(stored)
    for name, raw in incoming.items():
        new = CapabilitySetting.model_validate(raw)
        old = stored.get(name)
        if by_user:
            base = old or CapabilitySetting(enabled=False, set_by=None)
            setting = base.model_copy(update={"user": new.user})
        else:
            setting = combine(old, new.model_copy(update={"user": None}))
        if setting.set_by is None and setting.user is None:
            merged.pop(name, None)
        else:
            merged[name] = setting
    return {name: setting.model_dump(mode="json") for name, setting in merged.items()}


def composition_keys(
    stored: dict[str, Any], category_type: str, ingredient_book: IngredientBook | None
) -> dict[str, Any]:
    """Record keys for a learned category type: the capability and the old keys (dual write).

    Learning writes composition on (with the ingredient book) or off; the merge in
    ``update_knowledge`` keeps a user's own choice (ADR-0005 precedence). The old
    ``category_type`` follows the effective result.
    """
    params = {INGREDIENT_KEY: ingredient_book.model_dump(mode="json")} if ingredient_book else {}
    learned = CapabilitySetting(enabled=category_type == COMPOSITION_DRIVEN, params=params)
    incoming = {COMPOSITION: learned.model_dump(mode="json")}
    merged = merge_capabilities(stored_capabilities(stored) or {}, incoming, by_user=False)
    on = composition_book({COMPOSITION: CapabilitySetting.model_validate(merged[COMPOSITION])})
    return {
        CATEGORY_TYPE_KEY: COMPOSITION_DRIVEN if on is not None else SPEC_DRIVEN,
        CAPABILITIES_KEY: incoming,
    }


def resolve_fields(
    query_slug: str, own: CategoryProfile | None, parent: CategoryProfile | None
) -> ResolvedProfile:
    """Each book from ``own``, else from ``parent`` (one step), with the slug that owns it;
    capabilities by name, ``own``'s over ``parent``'s (``combine``)."""
    sources = [p for p in (own, parent) if p is not None]

    def pick(field: str) -> tuple[Any, str | None]:
        for profile in sources:
            value = getattr(profile, field)
            if value is not None:
                return value, profile.slug
        return None, None

    brand, brand_owner = pick("brand_book")
    model, model_owner = pick("model_book")
    capabilities: dict[str, CapabilitySetting] = {}
    for profile in reversed(sources):
        capabilities = combine_all(capabilities, profile.capabilities or {})
    return ResolvedProfile(
        slug=query_slug,
        brand_book=brand,
        model_book=model,
        capabilities=capabilities,
        brand_book_owner=brand_owner,
        model_book_owner=model_owner,
        found=own.slug if own is not None else None,
    )
