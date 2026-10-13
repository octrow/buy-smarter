"""Tests for review-trust heuristics. Pure math, no LLM/network."""

import pytest
from market_sources.base import RawReview, RawReviewStats

from buysmarter.ranking.review_trust import (
    _TRUST_FLOOR,
    count_defect_complaints,
    filter_junk,
    is_junk,
    review_trust,
)

_SUBSTANTIVE = "подробный отзыв про этот ирригатор, всё отлично"


def _review(
    rating: float | None = 4, text: str = "нормальный товар работает", date=None
) -> RawReview:
    return RawReview(source="kaspi", product_id="1", rating=rating, text=text, created_at=date)


def test_empty_reviews_are_trusted() -> None:
    trust, note = review_trust([])
    assert trust == 1.0
    assert "no reviews" in note


def test_organic_reviews_get_full_trust() -> None:
    reviews = [
        _review(rating=r, text="подробный отзыв про этот ирригатор, всё отлично", date=d)
        for r, d in [(5, "01.01.2023"), (4, "15.03.2023"), (3, "20.07.2023"), (5, "02.11.2023")]
    ]
    trust, note = review_trust(reviews)
    assert trust == 1.0
    assert "organic" in note


def test_all_five_star_large_sample_is_suspicious() -> None:
    # >20 rated reviews, all 5-star → rating dispersion signal fires (only that
    # one: long text, no dates).
    reviews = [
        _review(rating=5, text="прекрасный ирригатор, всем рекомендую однозначно")
        for _ in range(25)
    ]
    trust, note = review_trust(reviews)
    assert trust == 0.5
    assert "100% five-star" in note


def test_small_all_five_star_sample_not_penalized_on_rating() -> None:
    # Only 10 five-star reviews (<= 20) → too small to judge dispersion.
    reviews = [_review(rating=5, text="прекрасный ирригатор рекомендую всем") for _ in range(10)]
    trust, _ = review_trust(reviews)
    assert trust == 1.0


def test_date_clustering_is_suspicious() -> None:
    # 10 reviews all within a few days → >50% inside a 14-day window.
    reviews = [
        _review(
            rating=(4 if i % 2 else 5),
            text="достаточно длинный текст отзыва тут",
            date=f"0{i + 1}.05.2023",
        )
        for i in range(9)
    ]
    trust, note = review_trust(reviews)
    assert trust == 0.6
    assert "dates clustered" in note


def test_low_text_quality_is_suspicious() -> None:
    # >70% empty/short texts, ratings mixed and sample small (no rating signal).
    # Short but not junk (4 words, <15 chars) so the junk signal stays silent.
    reviews = [_review(rating=(4 if i % 2 else 5), text="а я и он") for i in range(10)]
    trust, note = review_trust(reviews)
    assert trust == 0.6
    assert "empty text" in note


def test_combined_signals_hit_the_floor() -> None:
    # All three signals fire: 0.5 * 0.6 * 0.6 = 0.18 → floored to 0.3.
    reviews = [_review(rating=5, text="", date="01.05.2023") for _ in range(25)]
    trust, note = review_trust(reviews)
    assert trust == _TRUST_FLOOR
    assert "five-star" in note and "dates clustered" in note and "empty text" in note


@pytest.mark.parametrize(
    "text",
    [
        "Ещё не пользовался, позже напишу",
        "Еще не пользовался, позже напишу",  # ё/е fold
        "Пока не тестировал, отзыв дополню",
        "Не успел проверить, потом дополню",
        "Беру на будущую покупку, пять звезд авансом",
    ],
)
def test_not_used_yet_review_is_junk(text: str) -> None:
    assert is_junk(_review(text=text))


def test_logistics_only_review_is_junk() -> None:
    assert is_junk(_review(text="Курьер привёз быстро, спасибо большое за доставку"))


def test_logistics_with_product_substance_is_not_junk() -> None:
    assert not is_junk(_review(text="Курьер привёз быстро, ноутбук работает отлично"))


def test_long_logistics_only_review_is_not_junk() -> None:
    text = (
        "Курьер приехал в срок, упаковка аккуратная, менеджер был на связи, "
        "все вопросы решили быстро и без лишней бюрократии, "
        "спасибо большое команде магазина за помощь"
    )
    assert not is_junk(_review(text=text))


@pytest.mark.parametrize("text", ["Норм", "Отлично!", "Вкусно очень круто"])
def test_low_info_review_is_junk(text: str) -> None:
    assert is_junk(_review(text=text))


def test_three_words_with_comma_are_not_low_info() -> None:
    assert not is_junk(_review(text="Вкусно, очень круто"))


def test_rating_only_short_review_is_junk() -> None:
    assert is_junk(_review(text="Поставил 5 звезд"))


def test_rating_phrase_with_substance_is_not_junk() -> None:
    text = (
        "Поставил пять звезд, но сначала пару недель тестировал: "
        "мощность хорошая, шум умеренный, корпус крепкий"
    )
    assert not is_junk(_review(text=text))


def test_empty_review_is_junk() -> None:
    assert is_junk(_review(text="   "))


def test_substantive_review_is_not_junk() -> None:
    assert not is_junk(
        _review(text="Ирригатор работает, струя мощная, за месяц ни разу не греется")
    )


def test_filter_junk_splits_reviews_and_reports_junk_share() -> None:
    good_1 = _review(text=_SUBSTANTIVE)
    good_2 = _review(text="Струя сильная, батарея держит неделю, шум терпимый")
    kept, share = filter_junk(
        [_review(text="Норм"), good_1, _review(text="Ещё не пользовался"), good_2]
    )
    assert kept == [good_1, good_2]
    assert share == 0.5


def test_filter_junk_on_empty_input_has_zero_share() -> None:
    assert filter_junk([]) == ([], 0.0)


def test_count_defect_complaints_counts_reviews_with_defect_words() -> None:
    defect_texts = [
        "Пришёл с браком",
        "Через месяц вышел из строя",
        "Похоже палёный, не оригинал",
        "Подделка, сразу видно",
    ]
    reviews = [_review(text=t) for t in defect_texts] + [_review(text=_SUBSTANTIVE)]
    assert count_defect_complaints(reviews) == len(defect_texts)


def test_mostly_junk_reviews_are_suspicious() -> None:
    # 6 of 10 junk (60%) → junk signal fires; short-text share 60% stays under 70%.
    reviews = [_review(rating=4, text="Норм") for _ in range(6)] + [
        _review(rating=4, text=_SUBSTANTIVE) for _ in range(4)
    ]
    trust, note = review_trust(reviews)
    assert trust == 0.7
    assert "60% junk reviews" in note


def test_half_junk_reviews_are_not_suspicious() -> None:
    # Exactly 50% is not above the 0.5 threshold.
    reviews = [_review(rating=4, text="Норм") for _ in range(5)] + [
        _review(rating=4, text=_SUBSTANTIVE) for _ in range(5)
    ]
    trust, _ = review_trust(reviews)
    assert trust == 1.0


@pytest.mark.parametrize("text", ["Не работает", "Брак", "Сломался через день"])
def test_short_defect_complaint_is_not_junk(text: str) -> None:
    assert not is_junk(_review(text=text))


# --- stats-based five-star share + unverified purchases + defect cons -------


def test_five_star_share_uses_full_stats_distribution_over_sample() -> None:
    # Sample: 9 four-star reviews (too small to judge). Full distribution says
    # 96 of 100 are five-star → the stats signal fires.
    reviews = [_review(rating=4, text=_SUBSTANTIVE) for _ in range(9)]
    stats = RawReviewStats(source="kaspi", product_id="1", rating_counts={5: 96, 4: 4}, total=100)
    trust, note = review_trust(reviews, stats)
    assert trust == 0.5
    assert "96% five-star" in note


def test_small_stats_distribution_is_ignored_for_five_star() -> None:
    # Stats total 15 (<= 20) → not enough to judge; sample is 4-star → no penalty.
    reviews = [_review(rating=4, text=_SUBSTANTIVE) for _ in range(25)]
    stats = RawReviewStats(source="kaspi", product_id="1", rating_counts={5: 15}, total=15)
    trust, _ = review_trust(reviews, stats)
    assert trust == 1.0


def _purchased(flags: list[bool | None]) -> list[RawReview]:
    return [
        RawReview(source="kaspi", product_id="1", rating=4, text=_SUBSTANTIVE, purchased=f)
        for f in flags
    ]


def test_mostly_unverified_purchases_are_suspicious() -> None:
    # 4 of 6 known purchases are unverified (67% > 50%).
    reviews = _purchased([False, False, False, False, True, True])
    trust, note = review_trust(reviews)
    assert trust == 0.7
    assert "67% unverified purchases" in note


def test_unverified_purchase_signal_needs_minimum_known_sample() -> None:
    # Only 4 known purchase flags (< 5): unverified signal stays silent.
    trust, _ = review_trust(_purchased([False, False, False, False]))
    assert trust == 1.0


def test_unknown_purchase_flags_do_not_count_as_unverified() -> None:
    trust, _ = review_trust(_purchased([None] * 8))
    assert trust == 1.0


def test_defect_complaint_found_in_cons_field() -> None:
    reviews = [
        RawReview(source="kaspi", product_id="1", text="нормальный товар", cons="сломался"),
        _review(text="нормальный товар работает"),
    ]
    assert count_defect_complaints(reviews) == 1


def test_defect_complaint_without_cons_still_matches_text() -> None:
    reviews = [_review(text="пришёл брак"), _review(text="всё хорошо")]
    assert count_defect_complaints(reviews) == 1


@pytest.mark.parametrize(
    "text", ["Пришёл без брака, всё отлично", "Не подделка, оригинал", "Ни одного дефекта за год"]
)
def test_negated_defect_words_are_not_complaints(text: str) -> None:
    assert count_defect_complaints([_review(text=text)]) == 0


def test_defect_complaint_next_to_negated_praise_still_counts() -> None:
    review = _review(text="Упаковка без брака, но через неделю не работает")
    assert count_defect_complaints([review]) == 1
