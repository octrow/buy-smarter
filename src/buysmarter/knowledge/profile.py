"""Category profile: one stored knowledge record read as books, capabilities and a parent.

A profile lives in the same ``category_knowledge.schema_json`` as before (ADR-0005,
docs/plans/2026-10-22-capabilities-phase1.md decision 1). New keys: ``capabilities``
(``{name: {enabled, params, set_by}}``), ``synonyms``, ``parent``, ``updated_by``,
``heal_checked``. Old records are read through an adapter: ``category_type ==
composition_driven`` becomes the composition capability with the ingredient book as its
parameter. Until phase 3 the old keys are written too (``composition_keys``), so an older
app version still reads the record.

Inheritance is one step: a field the profile does not set comes from its parent, and the
resolved profile names the owner of each field, so knowledge learned while using a book
is saved where the book lives.
"""

from typing import Any, Literal, TypeGuard

from pydantic import BaseModel, Field

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

# Who switched a capability on, strongest first (ADR-0005 "Category profile").
SetBy = Literal["user", "learned", "jev", "fallback"]


class CapabilitySetting(BaseModel):
    """One capability in a profile: on or off, its parameters, and who set it."""

    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    set_by: SetBy = "learned"


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


class ResolvedProfile(BaseModel):
    """What a query uses: each field after inheritance, and the slug that owns it.

    An owner is the query's own slug, its parent's, or the related profile the lookup
    found. Books from the static seed are owned by the query's slug (nothing stored owns
    them, so learned aliases go under the query, as before phase 1).
    """

    slug: str
    brand_book: BrandBook | None = None
    model_book: ModelBook | None = None
    capabilities: dict[str, CapabilitySetting] = Field(default_factory=dict)
    brand_book_owner: str | None = None
    model_book_owner: str | None = None
    capabilities_owner: str | None = None

    @property
    def ingredient_book(self) -> IngredientBook | None:
        return composition_book(self.capabilities)

    def enabled(self, name: str) -> CapabilitySetting | None:
        """The capability's setting when the profile switches it on, else ``None``."""
        setting = self.capabilities.get(name)
        return setting if _switched_on(setting) else None


def _switched_on(setting: CapabilitySetting | None) -> TypeGuard[CapabilitySetting]:
    return setting is not None and setting.enabled


def composition_book(capabilities: dict[str, CapabilitySetting]) -> IngredientBook | None:
    """The ingredient book of an enabled composition capability.

    No book stored reads as off, as an old composition record without one always did.
    """
    setting = capabilities.get(COMPOSITION)
    if _switched_on(setting) and (raw := setting.params.get(INGREDIENT_KEY)):
        return IngredientBook.model_validate(raw)
    return None


def _capabilities(raw: dict[str, Any]) -> dict[str, CapabilitySetting] | None:
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
        capabilities=_capabilities(raw),
        synonyms=list(raw.get(SYNONYMS_KEY) or []),
        parent=raw.get(PARENT_KEY) or None,
        heal_checked=bool(raw.get(HEAL_CHECKED_KEY)),
    )


def composition_keys(
    stored: dict[str, Any], category_type: str, ingredient_book: IngredientBook | None
) -> dict[str, Any]:
    """Record keys for a learned category type: the capability and the old keys (dual write).

    Other stored capabilities are kept; composition is switched on or removed. A user's
    own composition choice outranks learning (ADR-0005 precedence): it is kept, and only
    its ingredient book is refreshed while the user leaves it on.
    """
    capabilities = dict(stored.get(CAPABILITIES_KEY) or {})
    params = {INGREDIENT_KEY: ingredient_book.model_dump(mode="json")} if ingredient_book else {}
    current = capabilities.pop(COMPOSITION, None)
    setting = CapabilitySetting.model_validate(current) if current else None
    if setting is not None and setting.set_by == "user":
        if setting.enabled:
            setting = setting.model_copy(update={"params": {**setting.params, **params}})
    elif category_type == COMPOSITION_DRIVEN:
        setting = CapabilitySetting(params=params)
    else:
        setting = None
    if setting is not None:
        capabilities[COMPOSITION] = setting.model_dump(mode="json")
    composition_on = _switched_on(setting)
    return {
        CATEGORY_TYPE_KEY: COMPOSITION_DRIVEN if composition_on else SPEC_DRIVEN,
        CAPABILITIES_KEY: capabilities,
    }


def resolve_fields(
    query_slug: str, own: CategoryProfile | None, parent: CategoryProfile | None
) -> ResolvedProfile:
    """Each field from ``own``, else from ``parent`` (one step), with the slug that owns it."""
    sources = [p for p in (own, parent) if p is not None]

    def pick(field: str) -> tuple[Any, str | None]:
        for profile in sources:
            value = getattr(profile, field)
            if value is not None:
                return value, profile.slug
        return None, None

    brand, brand_owner = pick("brand_book")
    model, model_owner = pick("model_book")
    capabilities, capabilities_owner = pick("capabilities")
    return ResolvedProfile(
        slug=query_slug,
        brand_book=brand,
        model_book=model,
        capabilities=capabilities or {},
        brand_book_owner=brand_owner,
        model_book_owner=model_owner,
        capabilities_owner=capabilities_owner,
    )
