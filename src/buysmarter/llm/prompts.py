"""Query-parsing prompts + a keyless regex fallback.

``parse_query`` uses the LLM adapter to extract ``QueryConstraints`` from a
free-text request. By default this runs through the local ``claude`` CLI (Claude
subscription, no API key needed), so LLM parsing is the norm, not the exception.
``parse_query_naive`` is a dependency-free fallback used only when the ``claude``
binary is missing or the call fails — it only pulls out budget + city and treats
the rest as the search category (no feature extraction, on purpose: substring
feature filters would over-reject in ranking's hard filter).
"""

import re

from buysmarter.llm.adapter import generate
from buysmarter.llm.budget import parse_budget
from buysmarter.ranking.models import QueryConstraints

# Query parsing is a trivial extraction task (pull a few fields out of one
# short sentence): the OmniRoute free-stack combo (what the old "haiku" alias
# resolved to) is enough.
QUERY_PARSE_MODEL = "free-stack"

# city surface form (lowercased) → canonical name used downstream.
_CITY_CANON = {
    "алматы": "Almaty",
    "almaty": "Almaty",
    "астана": "Astana",
    "astana": "Astana",
    "нур-султан": "Astana",
    "шымкент": "Shymkent",
    "shymkent": "Shymkent",
    "караганда": "Karaganda",
}

# Leading verbs / connectors that are noise in a search query.
_STOPWORDS = {"нужен", "нужна", "нужно", "нужны", "хочу", "куплю", "ищу", "до", "в", "за", "на"}


def parse_query_prompt(user_query: str) -> str:
    """Build the LLM prompt that extracts ``QueryConstraints`` fields as JSON."""
    return (
        "Extract structured shopping constraints from the user's request below. "
        "Return JSON with these fields:\n"
        "- category: the product type to search for (short search phrase)\n"
        "- city: delivery city if explicitly stated in the request, else null "
        "(do not guess a default)\n"
        "- budget_max: maximum price in KZT as an integer, or null\n"
        "- required_features: hard-requirement features (e.g. 'IPX7'), list\n"
        "- excluded_brands: brands to avoid, list\n"
        "- preferred_marketplaces: e.g. ['kaspi','ozon'], list\n"
        "- usage_notes: short free-text usage context, or null\n\n"
        "CRITICAL: ``category`` MUST be in RUSSIAN — the language of the "
        "marketplaces this feeds (Kaspi, Ozon, OLX are all Russian-language "
        "sites). If the user's request is in another language, or in a "
        "language-neutral form (brand names, model codes, standalone specs "
        'like "SPF 50"), TRANSLATE the category into Russian yourself; never '
        "leave it in English or untranslated. A category that stays in "
        "English or neutral form returns zero results on these marketplaces. "
        "``required_features`` MUST stay VERBATIM in the exact words the user "
        "used — do not translate these, they are matched against listing text "
        "as-is.\n"
        "A specific brand line or model the user names (iPhone 13, Dyson "
        "Supersonic, Roborock S8) is what they are shopping for: keep it in "
        "``category`` VERBATIM after the Russian product type, never replace it "
        "with the bare type and never move it to ``required_features``.\n"
        "Item condition (б/у, used, новый) is NOT a required feature: put it in "
        "``usage_notes`` — used listings are always searched separately.\n"
        'Example: request "SPF 50 sunscreen for face" -> '
        '{"category": "солнцезащитный крем для лица", '
        '"required_features": ["SPF 50"], ...}\n'
        'Example: request "iphone 13 б/у" -> '
        '{"category": "смартфон iPhone 13", "required_features": [], '
        '"usage_notes": "б/у", ...}\n\n'
        f"User request: {user_query}"
    )


async def parse_query(user_query: str) -> QueryConstraints:
    """LLM-backed parse of ``user_query`` into ``QueryConstraints``."""
    result = await generate(
        parse_query_prompt(user_query), QueryConstraints, model=QUERY_PARSE_MODEL
    )
    return result  # type: ignore[return-value]


def _extract_city(query: str) -> str | None:
    low = query.lower()
    for surface, canon in _CITY_CANON.items():
        if surface in low:
            return canon
    return None


def _extract_category(text: str) -> str:
    """The query without its budget (already cut), city and filler words."""
    for surface in _CITY_CANON:
        text = re.sub(re.escape(surface), " ", text, flags=re.I)
    # a dot splits words, but not a fraction ("1.8 м")
    words = re.split(r"[\s,;]+|(?<!\d)\.|\.(?!\d)", text)
    return " ".join(w for w in words if w and w.lower() not in _STOPWORDS).strip()


def parse_query_naive(user_query: str) -> QueryConstraints:
    """Keyless fallback: regex out budget + city, rest becomes the category.

    ``city`` is left ``None`` when the query doesn't mention one — callers
    (``services.recommend``) apply the stored-preference / "Алматы" default so
    the parsed-request panel can flag it as such.
    """
    budget = parse_budget(user_query)
    return QueryConstraints(
        category=_extract_category(budget.text) or user_query.strip(),
        city=_extract_city(user_query),
        budget_max=budget.budget_max,
    )
