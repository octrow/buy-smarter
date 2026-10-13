"""LLM generation + extraction of per-category BrandBook + ModelBook.

Two entry points, both routed through the mandatory LLM cache (``llm.adapter``):

* ``generate_category_knowledge`` — builds knowledge for ANY category from the
  model's own knowledge. This is a deep-knowledge task (real reasoning over the
  brand landscape of an arbitrary category, incl. CIS-region brands on
  Kaspi/Wildberries), so it consciously uses ``opus`` per the model policy in
  ``llm/adapter.py``.
* ``extract_category_knowledge`` — pulls the same two books out of the user's own
  research notes (a markdown file). This is a mechanical extraction from supplied
  text, so it uses ``sonnet``.
"""

from pydantic import BaseModel, Field, model_validator

from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.ingredients import IngredientBook
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.knowledge.store import COMPOSITION_DRIVEN, SPEC_DRIVEN
from buysmarter.llm.adapter import generate

# Deep-knowledge task (reason over an arbitrary category's brand landscape) —
# opus is a conscious choice per the model policy documented in llm/adapter.py.
GENERATE_KNOWLEDGE_MODEL = "opus"
# Extraction from user-supplied notes is mechanical — sonnet is enough.
EXTRACT_KNOWLEDGE_MODEL = "sonnet"


class CategoryKnowledgeResult(BaseModel):
    """LLM response envelope: the books + type for one product category.

    ``category_type`` classifies the category (docs/PLAN.md); ``ingredient_book``
    is populated only for ``composition_driven`` categories (cosmetics/pharma).
    """

    brand_book: BrandBook
    model_book: ModelBook
    category_type: str = SPEC_DRIVEN
    ingredient_book: IngredientBook = Field(default_factory=IngredientBook)

    @model_validator(mode="before")
    @classmethod
    def _hoist_nested_type(cls, data: object) -> object:
        """Lift ``category_type`` the model nested inside a book (live opus answer
        put it in ``model_book``), so the top-level default never silently wins."""
        if not isinstance(data, dict) or "category_type" in data:
            return data
        for book in ("model_book", "brand_book", "ingredient_book"):
            nested = data.get(book)
            if isinstance(nested, dict) and "category_type" in nested:
                return {**data, "category_type": nested["category_type"]}
        return data


def _normalize_type(category_type: str, ingredient_book: IngredientBook) -> str:
    """Coerce the model's classification to a known value (default spec-driven).

    The schema allows substances only for composition-driven categories, so a
    non-empty ingredient book outvotes a missing or garbled type.
    """
    if category_type == COMPOSITION_DRIVEN or ingredient_book.substances:
        return COMPOSITION_DRIVEN
    return SPEC_DRIVEN


# Web-search grounding (docs/PLAN.md "Ranking-фиксы"): parametric memory alone
# goes stale fast for a fast-moving category — this preamble is prepended to
# the generate prompt when ``web_search=True`` so the opus CLI call actually
# runs its built-in WebSearch tool before answering, instead of guessing from
# training data. CLI-only (``llm.adapter.generate(cli_tools="WebSearch")``).
_WEB_RESEARCH_GUIDE = (
    "Before answering, run several web searches to ground this in the CURRENT "
    "market (2026), not just what you already know:\n"
    '1. "лучшие <category>" / "best <category>" rankings and reviews for the '
    "current year;\n"
    "2. availability and popular brands for this category on Kaspi.kz, Ozon "
    "and Wildberries specifically for Kazakhstan/CIS shoppers;\n"
    "3. known counterfeit-prone or fake/mimicked brand names in this "
    "category (e.g. spaced-out or homoglyph spellings of a real brand). "
    "These must NOT get a tier — note in your reasoning that mimicked/"
    "counterfeit spellings belong in NEITHER `tiers` NOR `aliases`;\n"
    "4. current model lines with their codes/SKUs.\n\n"
    "Base tiers, aliases and model entries on what your searches actually "
    "confirm; still omit anything you cannot verify this way.\n\n"
)

_SCHEMA_GUIDE = (
    "Return JSON with two objects, `brand_book` and `model_book`.\n\n"
    "`brand_book`:\n"
    "- `tiers`: map each well-known brand name to a trust tier string:\n"
    '  - "A" = clinically/expert-proven category leaders;\n'
    '  - "B" = solid mid-tier, INCLUDING CIS-region brands sold on Kaspi.kz / '
    "Wildberries (e.g. reputable local/regional makers);\n"
    '  - "C" = budget but genuinely decent — do NOT include pure noname brands;\n'
    "- `aliases`: map alternate spellings to the canonical brand name, INCLUDING "
    'Cyrillic transliterations (e.g. "Сяоми" -> "Xiaomi", "Филипс" -> '
    '"Philips") and sub-brand names (e.g. "Mijia" -> "Xiaomi").\n\n'
    "`model_book`:\n"
    "- `entries`: a list of well-known model lines, each with:\n"
    "  - `canonical`: the clean product/model name;\n"
    "  - `brand`: its canonical brand (matching a brand_book tier key);\n"
    '  - `codes`: model codes / SKUs (e.g. "MEO703", "F300", "RL-670");\n'
    "  - `aliases`: common marketplace/OLX title variants for the same model.\n\n"
    "`category_type`: classify the category as one of:\n"
    f'  - "{SPEC_DRIVEN}" (default) — ranked on specs + brand (electronics, '
    "appliances, tools, irrigators...);\n"
    f'  - "{COMPOSITION_DRIVEN}" — cosmetics, pharmacy, or supplements where the '
    "ACTIVE SUBSTANCE and its concentration matter as much as (or more than) the "
    "brand (e.g. an acne cream is defined by adapalene/benzoyl peroxide, not the "
    "brand name).\n\n"
    f'`ingredient_book`: ONLY for "{COMPOSITION_DRIVEN}" categories (leave '
    "its `substances` list empty otherwise). List the canonical active "
    "substances of this category, each with:\n"
    '  - `canonical`: the active substance name in Russian (e.g. "адапален", '
    '"бензоила пероксид", "азелаиновая кислота");\n'
    "  - `aliases`: spelling/latin/brand-as-substance variants (e.g. "
    '"adapalene", "Differin", "Дифферин");\n'
    '  - `typical_concentrations`: common strengths (e.g. ["0.1%", "0.3%"]);\n'
    "  - `equivalents`: known products sharing this substance, each `{name, "
    "concentration}` — the generic-equivalence group (e.g. Клензит == Differin "
    "== Адаклин by adapalene 0.1%).\n\n"
    "Prefer real, verifiable brands, models and substances over guesses. Omit "
    "anything you are unsure exists."
)


def generate_prompt(category: str, *, web_search: bool = True) -> str:
    """Prompt asking the model to build both books for ``category`` from scratch.

    When ``web_search``, the research preamble (``_WEB_RESEARCH_GUIDE``) is
    inserted before the schema guide so the model searches before answering;
    the schema guide itself is unchanged either way.
    """
    preamble = _WEB_RESEARCH_GUIDE if web_search else ""
    return (
        f"You are a product-category expert. For the category: {category!r} "
        "(a shopping category on Kazakhstani/CIS marketplaces such as Kaspi.kz, "
        "Wildberries, Ozon and OLX), produce brand-trust and product-identity "
        "knowledge.\n\n" + preamble + _SCHEMA_GUIDE
    )


def extract_prompt(category: str, notes: str) -> str:
    """Prompt asking the model to extract both books from the user's own notes."""
    return (
        f"Extract brand-trust and product-identity knowledge for the category "
        f"{category!r} from the user's research notes below. Use ONLY facts "
        "present in the notes; do not invent brands or models that are not "
        "mentioned.\n\n" + _SCHEMA_GUIDE + f"\n\n--- USER NOTES ---\n{notes}"
    )


CategoryKnowledge = tuple[BrandBook, ModelBook, str, IngredientBook]


# Web-search calls are slower than a pure-reasoning one — give the CLI enough
# room for several searches plus the actual generation.
_WEB_SEARCH_TIMEOUT = 600


async def generate_category_knowledge(
    category: str, *, web_search: bool = True
) -> CategoryKnowledge:
    """Generate books + type + ingredients for any category (opus, cached).

    One opus call classifies the category and, for composition-driven ones,
    returns the ingredient book too — no extra LLM call (docs/PLAN.md).

    ``web_search`` (default True) grounds the call in current (2026) market
    data via the local claude CLI's built-in WebSearch tool (CLI-only —
    ``llm.adapter.generate`` ignores ``cli_tools``/``cli_timeout`` for API
    providers). The prompt text changes with the flag, so the mandatory LLM
    cache naturally keys web-search and non-web-search results separately.
    """
    prompt = generate_prompt(category, web_search=web_search)
    if web_search:
        result = await generate(
            prompt,
            CategoryKnowledgeResult,
            model=GENERATE_KNOWLEDGE_MODEL,
            cli_tools="WebSearch",
            cli_timeout=_WEB_SEARCH_TIMEOUT,
        )
    else:
        result = await generate(prompt, CategoryKnowledgeResult, model=GENERATE_KNOWLEDGE_MODEL)
    return (
        result.brand_book,  # type: ignore[attr-defined]
        result.model_book,  # type: ignore[attr-defined]
        _normalize_type(result.category_type, result.ingredient_book),  # type: ignore[attr-defined]
        result.ingredient_book,  # type: ignore[attr-defined]
    )


async def extract_category_knowledge(category: str, notes: str) -> CategoryKnowledge:
    """Extract books + type + ingredients from user research notes (sonnet, cached)."""
    result = await generate(
        extract_prompt(category, notes), CategoryKnowledgeResult, model=EXTRACT_KNOWLEDGE_MODEL
    )
    return (
        result.brand_book,  # type: ignore[attr-defined]
        result.model_book,  # type: ignore[attr-defined]
        _normalize_type(result.category_type, result.ingredient_book),  # type: ignore[attr-defined]
        result.ingredient_book,  # type: ignore[attr-defined]
    )
