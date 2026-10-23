"""Exclude: substances the goods must not contain ("без сульфатов"), a request capability."""

import pytest

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.composition import required_substances
from buysmarter.capabilities.exclude import contains, excluded_in, strip_exclusions
from buysmarter.capabilities.flow import activate, read_query
from buysmarter.knowledge.ingredients import ActiveSubstance, IngredientBook
from buysmarter.knowledge.profile import ResolvedProfile
from buysmarter.llm.prompts import parse_query_naive, parse_query_prompt
from buysmarter.ranking.engine import rank
from buysmarter.ranking.models import Candidate, QueryConstraints
from buysmarter.services.recommend import relevance_phrase
from tests.fakes import composition_profile


@pytest.mark.parametrize(
    ("query", "excluded"),
    [
        ("шампунь без сульфатов", ["сульфаты"]),
        ("крем без парабенов и без сульфатов", ["парабены", "сульфаты"]),
        ("sulfate-free shampoo", ["сульфаты"]),
        ("конфеты без сахара", ["сахар"]),
        ("безлактозное молоко", ["лактоза"]),
        ("наушники без проводов", []),
        ("шампунь без парабенов и сульфатов", ["парабены", "сульфаты"]),
        ("шампунь без сульфатов и парабенов", ["сульфаты", "парабены"]),
        ("без сахара и лактозы", ["сахар", "лактоза"]),
        ("крем без SLS, парабенов и без сахара", ["сульфаты", "парабены", "сахар"]),
        ("наушники с ANC без JBL", []),
        ("шампунь без сульфатов, парабенов", ["сульфаты", "парабены"]),
        ("шампунь без сульфатов парабенов", ["сульфаты", "парабены"]),
        ("шампунь без сульфатов, парабенов и силиконов", ["сульфаты", "парабены"]),
        ("шампунь без сульфатов силиконов", ["сульфаты"]),
        ("бессульфатный шампунь", ["сульфаты"]),
        ("Cosmex Бессульфатный с кератином шампунь", ["сульфаты"]),
        ("бесплатная доставка", []),
    ],
)
def test_excluded_substances_are_read_from_the_query(query, excluded):
    assert excluded_in(query) == excluded


@pytest.mark.parametrize(
    ("text", "substance", "found"),
    [
        ("Aqua, Sodium Laureth Sulfate, Glycerin", "сульфаты", True),
        ("вода, лауретсульфат натрия, глицерин", "сульфаты", True),
        ("Магния сульфат 200 мг", "сульфаты", False),
        ("Sulfate-free formula, Aqua", "сульфаты", False),
        ("Без SLS, вода, глицерин", "сульфаты", False),
        ("Aqua, Methylparaben", "парабены", True),
        ("не содержит парабенов", "парабены", False),
        ("молоко, сахар, какао", "сахар", True),
        ("без добавления сахара, сахарозаменитель", "сахар", False),
        ("молоко безлактозное", "лактоза", False),
        ("молоко, лактоза", "лактоза", True),
    ],
)
def test_contains_skips_negations_and_lookalikes(text, substance, found):
    assert contains(text, substance) is found


def _read(query: str, constraints: QueryConstraints | None = None) -> QueryConstraints:
    constraints = constraints or parse_query_naive(query)
    return read_query(REGISTRY, constraints, query=query, use_llm=False)


def test_the_phrase_stays_searched_but_leaves_the_features():
    parsed = QueryConstraints(
        search_phrase="шампунь без сульфатов",
        product_type="шампунь",
        required_features=["без сульфатов", "для жирных волос"],
        exclude_substances=["SLS"],
    )

    read = _read("шампунь без сульфатов", parsed)

    assert read.exclude_substances == ["сульфаты"]
    assert read.search_phrase == "шампунь без сульфатов"
    assert read.required_features == ["для жирных волос"]
    assert strip_exclusions(read.search_phrase, read.exclude_substances) == "шампунь"


def _cand(pid: str, title: str, composition: str | None = None) -> Candidate:
    specs = {"Состав": composition} if composition else {}
    return Candidate(source="kaspi", source_product_id=pid, title=title, price=1_000, specs=specs)


def test_proven_presence_rejects_missing_composition_is_a_con():
    constraints = _read("шампунь без сульфатов")
    active = activate(REGISTRY, ResolvedProfile(slug="x"), constraints, query="", use_llm=False)
    pool = [
        _cand("1", "Шампунь A", "Aqua, Sodium Lauryl Sulfate"),
        _cand("2", "Шампунь B", "Aqua, Coco Glucoside"),
        _cand("3", "Шампунь C"),
        _cand("4", "Шампунь D без сульфатов"),
    ]

    rec = rank(pool, constraints, query="", hooks=active.rank_hooks())

    assert rec.rejected_reasons == {"excluded substance": 1}
    by_id = {s.candidate.source_product_id: s for s in rec.ranked}
    assert "состав не проверен на сульфаты" in by_id["3"].cons
    assert "без сульфаты по названию" not in by_id["2"].pros
    assert "без сульфатов по названию" in by_id["4"].pros
    assert not any(c.startswith("состав не проверен") for c in by_id["2"].cons)


def test_a_composition_category_does_not_require_the_excluded_substance():
    book = IngredientBook(substances=[ActiveSubstance(canonical="сульфат", aliases=["sulfate"])])
    constraints = _read("шампунь без сульфатов")

    assert required_substances(book, constraints) == []
    active = activate(REGISTRY, composition_profile(book), constraints, query="", use_llm=False)
    assert active.searches() == []


def test_the_prompt_moves_without_x_to_exclude_substances():
    assert '"exclude_substances": ["сульфаты"]' in parse_query_prompt("шампунь")


def test_the_relevance_filter_judges_the_phrase_without_the_exclusion():
    constraints = _read("шампунь без сульфатов")

    assert relevance_phrase(constraints) == "шампунь"


def test_a_whole_exclusion_list_leaves_the_phrase():
    names = ["парабены", "сульфаты"]

    assert strip_exclusions("шампунь без парабенов и сульфатов для волос", names) == (
        "шампунь для волос"
    )


def test_the_exclusion_leaves_the_naive_product_type():
    assert _read("шампунь без сульфатов").product_type == "шампунь"


@pytest.mark.parametrize(
    "query",
    [
        "шампунь без сульфатов и парабенов",
        "шампунь без сульфатов, парабенов",
        "шампунь без сульфатов парабенов",
    ],
)
def test_every_substance_of_a_without_list_is_excluded_and_leaves_the_phrase(query):
    read = _read(query)

    assert read.exclude_substances == ["сульфаты", "парабены"]
    assert read.product_type == "шампунь"
    assert relevance_phrase(read) == "шампунь"
