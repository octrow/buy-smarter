"""Dims: the tyre or mattress size the query names, a request capability."""

from typing import Any

import pytest

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.dims import dims_in
from buysmarter.capabilities.flow import activate, read_query
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.profile import ResolvedProfile
from buysmarter.llm.prompts import parse_query_naive, parse_query_prompt
from buysmarter.ranking.engine import rank
from buysmarter.ranking.models import Candidate, QueryConstraints
from buysmarter.services import official_stores
from buysmarter.services.official_stores import StoreOffer, official_candidates
from buysmarter.services.recommend import _filter_used
from buysmarter.services.understanding import NeedsClarification, ask_capability


@pytest.mark.parametrize(
    ("text", "dims"),
    [
        ("шины 205/55 R16 зимние", {"tyre": "205/55 R16"}),
        ("Шина Nokian 205/55R16 94T", {"tyre": "205/55 R16"}),
        ("резина 205/55 Р16", {"tyre": "205/55 R16"}),
        ("шина 225/45 ZR17", {"tyre": "225/45 R17"}),
        ("шина 195/70 R15C", {"tyre": "195/70 R15"}),
        ("матрас 160х200", {"mattress": "160x200"}),
        ("матрас 200 на 160 ортопедический", {"mattress": "160x200"}),
        ("Матрас 90*200", {"mattress": "90x200"}),
        ("монитор 1600x2000", {}),
        ("Размер упаковки 50x50x160", {}),
    ],
)
def test_dims_are_read_and_normalized(text, dims):
    assert dims_in(text) == dims


def _read(query: str, constraints: QueryConstraints | None = None) -> QueryConstraints:
    constraints = constraints or parse_query_naive(query)
    return read_query(REGISTRY, constraints, query=query, use_llm=False)


def test_the_normalized_size_goes_to_the_search_not_to_the_features():
    parsed = QueryConstraints(
        search_phrase="матрас 160х200 ортопедический",
        product_type="матрас",
        required_features=["160х200"],
    )

    read = _read("матрас 160х200 ортопедический", parsed)

    assert read.dims == {"mattress": "160x200"}
    assert read.search_phrase == "матрас ортопедический 160x200"
    assert read.required_features == []


def test_a_parsed_size_is_normalized_and_searched():
    parsed = QueryConstraints(
        search_phrase="зимние шины", product_type="шины", dims={"tyre": "205/55r16"}
    )

    read = _read("шины 205/55R16 зимние", parsed)

    assert read.dims == {"tyre": "205/55 R16"}
    assert read.search_phrase == "зимние шины 205/55 R16"


def _active(query: str):
    constraints = _read(query)
    return constraints, activate(
        REGISTRY, ResolvedProfile(slug="x"), constraints, query=query, use_llm=False
    )


def test_another_size_in_the_title_is_rejected_a_missing_one_is_a_con():
    constraints, active = _active("шины 205/55 R16 зимние")
    pool = [
        Candidate(source="kaspi", source_product_id="1", title="Шина 205/55 R16 Nokian", price=1),
        Candidate(source="kaspi", source_product_id="2", title="Шина 195/65 R15 Nokian", price=1),
        Candidate(source="kaspi", source_product_id="3", title="Шина Nokian Hakka", price=1),
    ]

    rec = rank(pool, constraints, query="", hooks=active.rank_hooks())

    assert rec.rejected_reasons == {"other size": 1}
    by_id = {s.candidate.source_product_id: s for s in rec.ranked}
    assert "размер не проверен: 205/55 R16" in by_id["3"].cons
    assert "размер не проверен: 205/55 R16" not in by_id["1"].cons


def test_size_keys_reject_but_other_specs_do_not():
    constraints, active = _active("матрас 160х200")
    wrong = Candidate(
        source="kaspi",
        source_product_id="1",
        title="Матрас Askona",
        price=1,
        specs={"Ширина": "140 см", "Длина": "200 см"},
    )
    packed = Candidate(
        source="kaspi",
        source_product_id="2",
        title="Матрас Askona",
        price=1,
        specs={"Размер упаковки": "50x50x160", "Размер": "160x200 см"},
    )

    rec = rank([wrong, packed], constraints, query="", hooks=active.rank_hooks())

    assert [s.candidate.source_product_id for s in rec.ranked] == ["2"]
    assert rec.rejected_reasons == {"other size": 1}
    assert not any(c.startswith("размер не проверен") for c in rec.ranked[0].cons)


async def test_an_official_store_offer_of_another_size_is_dropped(monkeypatch):
    offers = [
        StoreOffer(name="Матрас Comfort 140x200", url="https://s.kz/1", price=100),
        StoreOffer(name="Матрас Comfort 160x200", url="https://s.kz/2", price=200),
    ]

    async def _fetch(client: Any, url: str) -> list[StoreOffer]:
        return offers

    monkeypatch.setattr(official_stores, "fetch_store_offers", _fetch)
    book = BrandBook(tiers={"Askona": "A"}, official_stores={"Askona": "https://s.kz"})
    constraints, active = _active("матрас 160х200")

    found = await official_candidates(book, constraints, active.reject_listing)

    assert [c.title for c in found] == ["Askona Матрас Comfort 160x200"]


@pytest.mark.parametrize("query", ["зимние шины", "автошины", "резина летняя", "матрас"])
def test_goods_that_need_a_size_ask_for_it(query):
    constraints, active = _active(query)

    with pytest.raises(NeedsClarification) as asked:
        ask_capability(active.question(), constraints, 0, False)

    assert "размер" in asked.value.clarification.question.lower()


def test_the_mattress_question_offers_the_usual_sizes_and_its_answer_is_read():
    constraints, active = _active("матрас")
    question = active.question()

    assert question is not None and "160×200" in question.options
    assert _read("матрас 160×200").dims == {"mattress": "160x200"}


@pytest.mark.parametrize("query", ["машинка стиральная", "резинка для волос", "наматрасник"])
def test_other_goods_never_ask(query):
    _, active = _active(query)

    assert active.question() is None


async def test_an_olx_ad_of_another_size_is_dropped():
    constraints, active = _active("матрас 160х200")
    ads = [
        Candidate(source="olx", source_product_id="1", title="Матрас 140х200 б/у", price=1),
        Candidate(source="olx", source_product_id="2", title="Матрас 160х200 б/у", price=1),
    ]

    kept = await _filter_used(ads, constraints, False, active.reject_listing)

    assert [c.source_product_id for c in kept] == ["2"]


def test_the_prompt_keeps_sizes_out_of_the_features():
    assert '"dims": {"tyre": "205/55 R16"}' in parse_query_prompt("шины")
