"""Tests for the no-LLM requirement check (B6): stem match, never refuted."""

from market_sources.base import RawReview

from buysmarter.llm.review_summary import RequirementCheck
from buysmarter.ranking.review_requirements import naive_requirement_checks


def _review(text: str) -> RawReview:
    return RawReview(source="kaspi", product_id="1", text=text)


def test_two_reviews_with_stem_confirm_the_feature() -> None:
    reviews = [
        _review("Отличная штука. Шумоподавление работает хорошо."),
        _review("Шумоподавление спасает в офисе"),
    ]
    [check] = naive_requirement_checks(reviews, ["шумоподавление"])
    assert check.verdict == "confirmed"
    assert check.mentions == 2
    assert check.quote == "Шумоподавление работает хорошо"


def test_single_mention_stays_insufficient() -> None:
    reviews = [_review("Шумоподавление работает"), _review("Просто норм")]
    [check] = naive_requirement_checks(reviews, ["шумоподавление"])
    assert check == RequirementCheck(feature="шумоподавление", mentions=1)


def test_negated_sentence_does_not_count() -> None:
    reviews = [
        _review("Не работает шумоподавление совсем"),
        _review("Без шумоподавления нет смысла"),
    ]
    [check] = naive_requirement_checks(reviews, ["шумоподавление"])
    assert check.verdict == "insufficient"


def test_complaint_neither_confirms_nor_refutes() -> None:
    reviews = [_review("IPX7 сломался"), _review("IPX7 сломался снова")]
    [check] = naive_requirement_checks(reviews, ["IPX7"])
    assert check.verdict == "insufficient"  # refuting needs a model; confirming would be wrong


def test_feature_with_only_short_cyrillic_words_is_insufficient() -> None:
    reviews = [_review("Есть до утра работает отлично"), _review("До утра держит заряд")]
    [check] = naive_requirement_checks(reviews, ["до"])
    assert check == RequirementCheck(feature="до")


def test_quote_is_a_verbatim_substring_of_its_review() -> None:
    text = "Ёмкость огромная, IPX7 держит воду."
    reviews = [_review(text), _review("IPX7 держит воду")]
    [check] = naive_requirement_checks(reviews, ["IPX7"])
    assert check.quote in text


def test_negation_after_the_stem_does_not_confirm() -> None:
    reviews = [
        _review("Шумоподавление не работает."),
        _review("Шумоподавление не работает вообще."),
    ]
    assert naive_requirement_checks(reviews, ["шумоподавление"])[0].verdict == "insufficient"


def test_defect_complaint_does_not_confirm() -> None:
    reviews = [_review("Шумоподавление сломалось через неделю.")] * 2
    assert naive_requirement_checks(reviews, ["шумоподавление"])[0].verdict == "insufficient"


def test_short_latin_feature_is_matched_whole() -> None:
    reviews = [
        _review("NFC отлично оплачивает в любом магазине."),
        _review("Оплата по NFC удобная, работает с первого раза."),
    ]
    assert naive_requirement_checks(reviews, ["NFC"])[0].verdict == "confirmed"
