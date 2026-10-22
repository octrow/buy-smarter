"""Request understanding: restricted goods and the clarification decision (phase 2)."""

import pytest

from buysmarter.capabilities.base import Capability, CapabilityContext, Question
from buysmarter.capabilities.flow import ActiveCapabilities, activate
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services.understanding import (
    Clarification,
    NeedsClarification,
    NothingToSearch,
    apply_answer,
    ask_capability,
    restricted_kind,
    restricted_note,
    settle_request,
)


@pytest.mark.parametrize(
    ("query", "kind"),
    [
        ("вейп", "tobacco"),
        ("жижа для вейпа", "tobacco"),
        ("электронная сигарета", "tobacco"),
        ("сигареты Marlboro", "tobacco"),
        ("табак для кальяна", "tobacco"),
        ("кальян", "tobacco"),
        ("вино красное сухое", "alcohol"),
        ("пиво", "alcohol"),
        ("водка", "alcohol"),
        ("коньяк армянский", "alcohol"),
        ("виски", "alcohol"),
        ("алкоголь на праздник", "alcohol"),
        ("травмат", "weapon"),
        ("травматический пистолет", "weapon"),
        ("нож-бабочка", "weapon"),
    ],
)
def test_the_static_list_flags_restricted_goods(query: str, kind: str) -> None:
    assert restricted_kind(query) == kind


@pytest.mark.parametrize(
    "query",
    [
        "винный бокал",
        "бокал для вина",
        "пивная кружка",
        "кружка для пива",
        "травматологический ортез",
        "кальянный уголь",
        "уголь для кальяна",
        "пневматическая винтовка",
        "безалкогольное пиво",
        "винтовка",
        "кроссовки",
    ],
)
def test_stop_contexts_and_other_goods_are_not_restricted(query: str) -> None:
    assert restricted_kind(query) is None


def test_each_kind_has_a_warning_and_none_has_none() -> None:
    for kind in ("rx", "alcohol", "tobacco", "weapon"):
        assert restricted_note(kind)
    assert restricted_note(None) is None


# --- The clarification decision -------------------------------------------------


def _parsed(intent: str, options: list[str] | None = None, **fields: object) -> QueryConstraints:
    return QueryConstraints.model_validate({"intent": intent, "options": options or [], **fields})


def _asked(constraints: QueryConstraints, clarify_round: int = 0) -> Clarification:
    with pytest.raises(NeedsClarification) as caught:
        settle_request(constraints, clarify_round=clarify_round, no_clarify=False)
    return caught.value.clarification


def test_a_product_request_is_searched_as_parsed() -> None:
    parsed = _parsed("product", search_phrase="мужские кроссовки")

    assert settle_request(parsed, clarify_round=0, no_clarify=False) == (parsed, None)


@pytest.mark.parametrize(
    ("intent", "options"),
    [
        ("open", ["духи", "плед"]),
        ("need", ["мазь", "пластырь"]),
        ("multi", ["iPhone 15", "Samsung S24"]),
    ],
)
def test_an_ambiguous_request_gets_a_question_with_options(intent: str, options: list[str]):
    question = _asked(_parsed(intent, options), clarify_round=1)

    assert question.kind == intent
    assert question.options == options
    assert question.round == 1
    assert question.question


@pytest.mark.parametrize("intent", ["unclear", "service"])
def test_unreadable_and_service_requests_get_a_question_without_options(intent: str) -> None:
    question = _asked(_parsed(intent))

    assert question.kind == intent
    assert question.options == []


@pytest.mark.parametrize("intent", ["open", "need"])
def test_open_and_need_without_options_or_phrase_are_unclear(intent: str) -> None:
    assert _asked(_parsed(intent)).kind == "unclear"


def test_a_product_with_a_blank_phrase_is_unclear() -> None:
    """Defence in depth: never search "" (an empty parse once showed pens for 10 ₸)."""
    assert _asked(_parsed("product", search_phrase="  ")).kind == "unclear"


def test_a_need_one_product_type_solves_is_searched() -> None:
    parsed = _parsed("need", need="перхоть", search_phrase="шампунь от перхоти")

    assert settle_request(parsed, clarify_round=0, no_clarify=False) == (parsed, None)


def test_a_multi_with_one_product_is_searched() -> None:
    parsed = _parsed("multi", ["коляска"], search_phrase="коляска")

    assert settle_request(parsed, clarify_round=0, no_clarify=False)[1] is None


@pytest.mark.parametrize(("clarify_round", "no_clarify"), [(2, False), (0, True)])
def test_the_last_round_searches_the_first_option(clarify_round: int, no_clarify: bool) -> None:
    parsed = _parsed("open", ["крем для рук", "плед"], budget_max=15000, city="Astana")

    settled, assumed = settle_request(parsed, clarify_round=clarify_round, no_clarify=no_clarify)

    assert assumed == "крем для рук"
    assert settled.intent == "product"
    assert settled.search_phrase == "крем для рук"
    assert (settled.budget_max, settled.city) == (15000, "Astana")


@pytest.mark.parametrize("intent", ["unclear", "service", "open"])
def test_the_last_round_without_options_has_nothing_to_search(intent: str) -> None:
    with pytest.raises(NothingToSearch) as caught:
        settle_request(_parsed(intent), clarify_round=2, no_clarify=False)

    assert str(caught.value)
    assert isinstance(caught.value, ValueError)
    assert not isinstance(NeedsClarification(Clarification(kind="open", question="?")), ValueError)


def test_a_restricted_open_request_carries_the_warning_into_the_question() -> None:
    question = _asked(_parsed("open", ["вино", "коньяк"], restricted="alcohol"))

    assert question.restricted_note == restricted_note("alcohol")


def test_an_answer_that_is_an_option_is_taken_without_a_new_parse() -> None:
    parsed = _parsed("multi", ["iPhone 15", "Samsung S24"], budget_max=500000, gender="male")

    chosen = apply_answer(parsed, " samsung s24 ")

    assert chosen is not None
    assert (chosen.intent, chosen.search_phrase) == ("product", "Samsung S24")
    assert (chosen.budget_max, chosen.gender) == (500000, "male")
    assert settle_request(chosen, clarify_round=1, no_clarify=False) == (chosen, None)


def test_an_own_answer_is_not_an_option() -> None:
    assert apply_answer(_parsed("open", ["духи"]), "шарф кашемировый") is None


class _AsksForTyreSize(Capability):
    name = "tyre_size"

    def setting(self, profile: ResolvedProfile, constraints: QueryConstraints):
        return CapabilitySetting(set_by="fallback")

    def clarify(self, ctx: CapabilityContext) -> Question | None:
        return Question("Какой размер шин?", ["205/55 R16", "195/65 R15"])


def _tyres() -> ActiveCapabilities:
    constraints = QueryConstraints(search_phrase="шины")
    registry: dict[str, Capability] = {"tyre_size": _AsksForTyreSize()}
    return activate(
        registry, ResolvedProfile(slug="шины"), constraints, query="шины", use_llm=False
    )


def test_a_capability_question_is_asked_as_a_capability_clarification() -> None:
    constraints = QueryConstraints(search_phrase="шины", restricted="weapon")

    with pytest.raises(NeedsClarification) as caught:
        ask_capability(_tyres().question(), constraints, clarify_round=0, no_clarify=False)

    asked = caught.value.clarification
    assert (asked.kind, asked.question) == ("capability", "Какой размер шин?")
    assert asked.options == ["205/55 R16", "195/65 R15"]
    assert asked.restricted_note == restricted_note("weapon")


@pytest.mark.parametrize(("clarify_round", "no_clarify"), [(2, False), (1, True)])
def test_the_last_round_searches_without_the_capability_parameter(
    clarify_round: int, no_clarify: bool
) -> None:
    constraints = QueryConstraints(search_phrase="шины")

    ask_capability(_tyres().question(), constraints, clarify_round, no_clarify)  # no raise


def test_no_capability_question_means_no_clarification() -> None:
    ask_capability(None, QueryConstraints(search_phrase="шины"), 0, False)  # no raise
