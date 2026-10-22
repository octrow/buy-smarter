"""Query-parsing prompts + a keyless regex fallback.

``parse_query`` uses the LLM adapter to extract ``QueryConstraints`` from a
free-text request. By default this runs through the local ``claude`` CLI (Claude
subscription, no API key needed), so LLM parsing is the norm, not the exception.
``parse_query_naive`` is a dependency-free fallback used only when the ``claude``
binary is missing or the call fails — it only pulls out budget + city and treats
the rest as the search phrase (no feature extraction, on purpose: substring
feature filters would over-reject in ranking's hard filter).
"""

import re

from buysmarter.llm.adapter import generate
from buysmarter.llm.budget import parse_budget
from buysmarter.ranking.audience import bare_product_type
from buysmarter.ranking.models import Intent, QueryConstraints

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
# A naive phrase with fewer letters than this cannot be read ("???", "1").
_MIN_LETTERS = 2
_USED_RE = re.compile(r"(?<!\w)(?:б/у|б\.у\.?|бу|used)(?!\w)", re.IGNORECASE)
_NEW_RE = re.compile(r"(?<!\w)(?:нов(?:ый|ая|ое|ые)|new)(?!\w)", re.IGNORECASE)
_STOPWORDS = {"нужен", "нужна", "нужно", "нужны", "хочу", "куплю", "ищу", "до", "в", "за", "на"}


_FIELDS = (
    "Return JSON with these fields:\n"
    "- intent: what kind of request it is, one of product, need, open, multi, service, "
    "unclear (see the examples)\n"
    "- search_phrase: the short Russian phrase to search the marketplaces for, with the "
    "audience, size, model and substance the user named\n"
    "- product_type: the short canonical Russian name of the goods, WITHOUT audience, size, "
    "compatibility or theme (кроссовки, шампунь, зарядка, смартфон)\n"
    "- need: the problem a need request names, else null\n"
    "- options: for open, need with several product types and multi: the concrete products "
    "to choose from (2-5, short Russian search phrases), else []\n"
    "- restricted: rx (prescription medicine), alcohol, tobacco (also vapes), weapon, or null\n"
    "- condition: used or new when the user says so (б/у, used, новый), else null\n"
    "- city: delivery city if explicitly stated in the request, else null "
    "(do not guess a default)\n"
    "- budget_max, budget_min: price limits in KZT as integers, or null\n"
    "- budget_note: why a named budget was not used (another currency), else null\n"
    "- required_features: hard-requirement features (e.g. 'IPX7'), list\n"
    "- excluded_brands: brands to avoid, list\n"
    "- preferred_marketplaces: e.g. ['kaspi','ozon'], list\n"
    "- usage_notes: short free-text usage context, or null\n\n"
)
_RULES = (
    "CRITICAL: ``search_phrase`` and ``product_type`` MUST be in RUSSIAN — the language of "
    "the marketplaces this feeds (Kaspi, Ozon, OLX are all Russian-language "
    "sites). If the user's request is in another language, or in a "
    "language-neutral form (brand names, model codes, standalone specs "
    'like "SPF 50"), TRANSLATE the phrase into Russian yourself; never '
    "leave it in English or untranslated. A phrase that stays in "
    "English or neutral form returns zero results on these marketplaces. "
    "``required_features`` MUST stay VERBATIM in the exact words the user "
    "used — do not translate these, they are matched against listing text "
    "as-is.\n"
    "A specific brand line or model the user names (iPhone 13, Dyson "
    "Supersonic, Roborock S8) is what they are shopping for: keep it in "
    "``search_phrase`` VERBATIM after the Russian product type, never replace it "
    "with the bare type and never move it to ``required_features``.\n"
    "Item condition (б/у, used, новый) is NOT a required feature: put it in "
    "``condition`` — used listings are always searched separately.\n"
    "Ask (open, need with several options, multi) ONLY when searching without an answer "
    "would be a guess. A need that one product type solves is a need with no options.\n"
    "Budget: 'тыс', 'косарь', 'мың' and 'штук' after до/за mean thousands of tenge; "
    "'до 50' means 50 000; 'N k' is money only from 10 up ('4K' is a screen); a unit after "
    "the number is never money ('до 2 м', 'до 18 кг', 'до 3 лет', 'до 5000 МЕ', '120 шт'); "
    "another currency ($, руб, €) gives no budget and a budget_note.\n"
)
_EXAMPLES = (
    'Example: "мужские кроссовки 42" -> {"intent": "product", '
    '"search_phrase": "мужские кроссовки", "product_type": "кроссовки", ...}\n'
    'Example: "SPF 50 sunscreen for face" -> {"intent": "product", '
    '"search_phrase": "солнцезащитный крем для лица", "product_type": "солнцезащитный крем", '
    '"required_features": ["SPF 50"], ...}\n'
    'Example: "iphone 13 б/у" -> {"intent": "product", '
    '"search_phrase": "смартфон iPhone 13", "product_type": "смартфон", '
    '"required_features": [], "condition": "used", ...}\n'
    'Example: "шампунь от перхоти" -> {"intent": "need", "need": "перхоть", '
    '"search_phrase": "шампунь от перхоти", "product_type": "шампунь", "options": [], ...}\n'
    'Example: "чем снять боль в спине" -> {"intent": "need", "need": "боль в спине", '
    '"options": ["мазь обезболивающая", "пластырь перцовый", "массажёр для спины"], ...}\n'
    'Example: "подарок для мамы до 15000" -> {"intent": "open", "budget_max": 15000, '
    '"options": ["духи", "крем для лица", "плед", "кофемашина"], ...}\n'
    'Example: "iPhone 15 или Samsung S24" -> {"intent": "multi", '
    '"options": ["iPhone 15", "Samsung S24"], ...}\n'
    'Example: "всё для новорождённого" -> {"intent": "multi", '
    '"options": ["коляска", "автокресло", "кроватка", "подгузники"], ...}\n'
    'Example: "ремонт телефона" -> {"intent": "service", ...}\n'
    'Example: "аыавыа" -> {"intent": "unclear", ...}\n'
    'Example: "вейп до 10к" -> {"intent": "product", "search_phrase": "вейп", '
    '"product_type": "вейп", "restricted": "tobacco", "budget_max": 10000, ...}\n\n'
)


def parse_query_prompt(user_query: str) -> str:
    """Build the LLM prompt that extracts ``QueryConstraints`` fields as JSON."""
    return (
        "Extract structured shopping constraints from the user's request below. "
        + _FIELDS
        + _RULES
        + _EXAMPLES
        + f"User request: {user_query}"
    )


class ParsedQuery(QueryConstraints):
    """The LLM's answer: ``intent`` and ``search_phrase`` must be there.

    With every field defaulted any JSON validated, so an off-schema answer became an empty
    "product" request (and was cached). Required here, a bad answer fails, the adapter
    retries, then the caller falls back to the naive parse.
    """

    intent: Intent
    search_phrase: str


async def parse_query(user_query: str) -> QueryConstraints:
    """LLM-backed parse of ``user_query``; the naive rules fill what the LLM left out.

    An empty product type becomes the naive one; no budget and no budget note from the LLM
    means the deterministic budget rules decide (``llm.budget``: units are never money).
    """
    answer = await generate(parse_query_prompt(user_query), ParsedQuery, model=QUERY_PARSE_MODEL)
    result = QueryConstraints.model_validate(answer.model_dump())
    updates: dict[str, object] = {}
    if not result.product_type.strip():
        updates["product_type"] = bare_product_type(result.search_phrase)
    if result.budget_max is None and result.budget_note is None:
        budget = parse_budget(user_query)
        updates.update(
            budget_max=budget.budget_max,
            budget_min=budget.budget_min,
            budget_note=budget.budget_note,
        )
    return result.model_copy(update=updates) if updates else result


def _condition(query: str) -> str | None:
    if _USED_RE.search(query):
        return "used"
    return "new" if _NEW_RE.search(query) else None


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
    phrase = _extract_category(budget.text)  # nothing left == unclear, never the whole query
    return QueryConstraints(
        search_phrase=phrase,
        product_type=bare_product_type(phrase),
        intent="product" if len(re.findall(r"[^\W\d_]", phrase)) >= _MIN_LETTERS else "unclear",
        city=_extract_city(user_query),
        budget_max=budget.budget_max,
        budget_min=budget.budget_min,
        budget_note=budget.budget_note,
        condition=_condition(user_query),
    )
