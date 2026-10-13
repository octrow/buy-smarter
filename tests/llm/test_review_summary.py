"""Tests for review summarization: prompt shape + generate wiring (stubbed)."""

from market_sources.base import RawReview

from buysmarter.llm import review_summary as rs
from buysmarter.llm.review_summary import (
    SUMMARIZE_REVIEWS_MODEL,
    RequirementCheck,
    ReviewSummary,
    summarize_reviews,
    summarize_reviews_prompt,
)


def test_model_choice_is_free_stack_combo() -> None:
    assert SUMMARIZE_REVIEWS_MODEL == "free-stack"


def test_prompt_includes_review_texts() -> None:
    prompt = summarize_reviews_prompt(["мощный напор", "быстро сел аккумулятор"])
    assert "мощный напор" in prompt
    assert "быстро сел аккумулятор" in prompt


async def test_summarize_reviews_calls_generate_with_free_stack(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        captured["prompt"] = prompt
        captured["model"] = model
        captured["response_model"] = response_model
        return ReviewSummary(
            pros=["напор"], cons=["шум"], common_complaints=["течёт"], verdict_line="ок"
        )

    monkeypatch.setattr(rs, "generate", _fake_generate)

    reviews = [
        RawReview(source="kaspi", product_id="1", text="отличный напор воды у прибора"),
        RawReview(source="kaspi", product_id="1", text=""),  # empty texts dropped
        RawReview(source="kaspi", product_id="1", text="шумит слишком сильно при работе"),
    ]
    result = await summarize_reviews(reviews)

    assert captured["model"] == "free-stack"
    assert captured["response_model"] is ReviewSummary
    assert "отличный напор" in captured["prompt"]
    assert "" not in [line for line in captured["prompt"].splitlines() if line.startswith("- ")]
    assert result.pros == ["напор"]


def test_prompt_asks_for_severity_fields() -> None:
    prompt = summarize_reviews_prompt(["не работает", "аллергия"])
    assert "red_flags" in prompt
    assert "counterfeit_suspicion" in prompt
    assert "reliability_concern" in prompt
    assert "negative_share_estimate" in prompt


def test_review_summary_defaults_are_benign() -> None:
    summary = ReviewSummary()
    assert summary.red_flags == []
    assert summary.counterfeit_suspicion is False
    assert summary.reliability_concern == "none"
    assert summary.negative_share_estimate == 0.0


async def test_summarize_reviews_parses_extended_fields(monkeypatch) -> None:
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return response_model(
            pros=[],
            cons=["жидкая консистенция"],
            common_complaints=["кожа сгорает"],
            verdict_line="подозрение на подделку",
            red_flags=["аллергические реакции", "кожа сгорает"],
            counterfeit_suspicion=True,
            reliability_concern="serious",
            negative_share_estimate=0.7,
        )

    monkeypatch.setattr(rs, "generate", _fake_generate)

    reviews = [RawReview(source="kaspi", product_id="1", text="крем не защищает, кожа сгорает")]
    result = await summarize_reviews(reviews)

    assert result.counterfeit_suspicion is True
    assert result.reliability_concern == "serious"
    assert "аллергические реакции" in result.red_flags
    assert result.negative_share_estimate == 0.7


async def test_summarize_reviews_without_texts_skips_llm(monkeypatch) -> None:
    async def _boom(*args, **kwargs):
        raise AssertionError("LLM must not be called with no review text")

    monkeypatch.setattr(rs, "generate", _boom)

    result = await summarize_reviews([RawReview(source="kaspi", product_id="1", text="  ")])

    assert result == ReviewSummary()


async def _captured_prompt(monkeypatch, reviews: list[RawReview]) -> str:
    captured: dict = {}

    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        captured["prompt"] = prompt
        return ReviewSummary()

    monkeypatch.setattr(rs, "generate", _fake_generate)
    await summarize_reviews(reviews)
    return captured["prompt"]


def _bullets(prompt: str) -> list[str]:
    """Review lines only: the bullets after the ``Reviews:`` marker."""
    block = prompt.split("Reviews:\n", 1)[1]
    return [line[2:] for line in block.splitlines() if line.startswith("- ")]


async def test_low_rated_reviews_are_listed_first(monkeypatch) -> None:
    reviews = [
        RawReview(source="kaspi", product_id="1", rating=5, text="отличный напор воды у прибора"),
        RawReview(source="kaspi", product_id="1", rating=2, text="шумит слишком сильно при работе"),
    ]

    bullets = _bullets(await _captured_prompt(monkeypatch, reviews))

    assert bullets[0].startswith("[2★] шумит")
    assert bullets[1].startswith("[5★] отличный")


async def test_junk_reviews_are_dropped_from_prompt(monkeypatch) -> None:
    reviews = [
        RawReview(source="kaspi", product_id="1", rating=5, text="Норм"),
        RawReview(source="kaspi", product_id="1", rating=4, text="отличный напор воды у прибора"),
    ]

    prompt = await _captured_prompt(monkeypatch, reviews)

    assert "Норм" not in prompt
    assert "отличный напор" in prompt


async def test_long_review_text_is_truncated(monkeypatch) -> None:
    long_text = "я" * 800
    reviews = [RawReview(source="kaspi", product_id="1", rating=4, text=long_text)]

    prompt = await _captured_prompt(monkeypatch, reviews)

    assert "я" * rs._MAX_REVIEW_CHARS in prompt
    assert "я" * (rs._MAX_REVIEW_CHARS + 1) not in prompt


async def test_unknown_rating_has_no_star_prefix(monkeypatch) -> None:
    reviews = [RawReview(source="kaspi", product_id="1", text="отличный напор воды у прибора")]

    bullets = _bullets(await _captured_prompt(monkeypatch, reviews))

    assert bullets == ["отличный напор воды у прибора"]


async def test_prompt_is_capped_at_max_reviews(monkeypatch) -> None:
    reviews = [
        RawReview(source="kaspi", product_id="1", rating=5, text=f"отличный напор воды номер {i}")
        for i in range(rs._MAX_REVIEWS + 10)
    ]

    assert len(_bullets(await _captured_prompt(monkeypatch, reviews))) == rs._MAX_REVIEWS


# --- Requirement checks (B1/B3/B4): features the query requires -------------


def test_prompt_without_features_is_byte_identical_to_legacy_template() -> None:
    # Pinned legacy prompt: changing it would invalidate every cached summary.
    expected = (
        "Summarize the following product reviews for a shopper. Respond in "
        "Russian. Each review may start with its star rating like [2★]. Extract:\n"
        "- pros: concrete positives buyers mention (list)\n"
        "- cons: concrete negatives buyers mention (list)\n"
        "- common_complaints: recurring complaints worth flagging (list)\n"
        "- verdict_line: one short sentence summarizing the overall sentiment\n"
        "- red_flags: severe issues stated plainly (safety, allergic reactions, "
        "burns, breaks within a year, doesn't perform its core function). "
        "Each a short Russian phrase. Empty list if none.\n"
        "- counterfeit_suspicion: true ONLY if reviews suspect the product is "
        "fake/counterfeit (e.g. wrong consistency, suspiciously cheap, differs "
        "from the original)\n"
        "- reliability_concern: 'serious' if reviews report early failure, "
        "safety issues, or that it does not do its core job; 'minor' for smaller "
        "recurring durability gripes; 'none' otherwise\n"
        "- negative_share_estimate: your estimate (0.0-1.0) of the share of "
        "reviews that are clearly negative\n\n"
        "Reviews:\n- мощный напор"
    )
    assert summarize_reviews_prompt(["мощный напор"], None) == expected
    assert summarize_reviews_prompt(["мощный напор"], []) == expected


def test_prompt_with_features_asks_for_verdict_mentions_and_quote() -> None:
    prompt = summarize_reviews_prompt(["шумит"], ["шумоподавление"])
    assert "шумоподавление" in prompt
    assert "verdict" in prompt and "mentions" in prompt and "quote" in prompt
    assert prompt.index("шумоподавление") < prompt.index("Reviews:\n")


def test_summary_defaults_have_no_requirement_checks() -> None:
    assert ReviewSummary().requirement_checks == []


async def test_summarize_with_features_sends_feature_section(monkeypatch) -> None:
    captured = await _captured_prompt_with(monkeypatch, ["IPX7"], _review_pair())
    assert "IPX7" in captured


def _review_pair() -> list[RawReview]:
    return [
        RawReview(source="kaspi", product_id="1", text="Пыль и влага не страшны, IPX7 честно"),
        RawReview(source="kaspi", product_id="1", text="Шумит сильно, но IPX7 держит"),
    ]


async def _captured_prompt_with(monkeypatch, features, reviews) -> str:
    captured: dict = {}

    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        captured["prompt"] = prompt
        return ReviewSummary()

    monkeypatch.setattr(rs, "generate", _fake_generate)
    await summarize_reviews(reviews, features)
    return captured["prompt"]


async def _run_with_llm(monkeypatch, checks: list[RequirementCheck], features, reviews):
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return ReviewSummary(requirement_checks=checks)

    monkeypatch.setattr(rs, "generate", _fake_generate)
    return await summarize_reviews(reviews, features)


async def test_fabricated_quote_becomes_insufficient(monkeypatch) -> None:
    checks = [
        RequirementCheck(
            feature="IPX7", verdict="confirmed", mentions=3, quote="держит воду как рыба"
        )
    ]
    result = await _run_with_llm(monkeypatch, checks, ["IPX7"], _review_pair())
    assert result.requirement_checks == [RequirementCheck(feature="IPX7", mentions=3)]


async def test_real_quote_with_two_mentions_is_kept(monkeypatch) -> None:
    checks = [
        RequirementCheck(feature="IPX7", verdict="confirmed", mentions=2, quote="IPX7 честно")
    ]
    result = await _run_with_llm(monkeypatch, checks, ["IPX7"], _review_pair())
    assert result.requirement_checks == [
        RequirementCheck(feature="IPX7", verdict="confirmed", mentions=2, quote="IPX7 честно")
    ]


async def test_real_quote_with_one_mention_is_insufficient(monkeypatch) -> None:
    checks = [
        RequirementCheck(feature="IPX7", verdict="confirmed", mentions=1, quote="IPX7 честно")
    ]
    result = await _run_with_llm(monkeypatch, checks, ["IPX7"], _review_pair())
    assert result.requirement_checks[0].verdict == "insufficient"
    assert result.requirement_checks[0].quote == ""


async def test_omitted_and_unrequested_features_are_normalized(monkeypatch) -> None:
    checks = [
        RequirementCheck(feature="Wi-Fi", verdict="refuted", mentions=2, quote="IPX7 честно"),
    ]
    result = await _run_with_llm(monkeypatch, checks, ["IPX7"], _review_pair())
    assert result.requirement_checks == [RequirementCheck(feature="IPX7")]


async def test_quote_match_ignores_case_yo_and_surrounding_quotes(monkeypatch) -> None:
    reviews = [
        RawReview(source="kaspi", product_id="1", text="Ёмкость  большая, пыль не лезет"),
        RawReview(source="kaspi", product_id="1", text="Ёмкость огромная и удобная"),
    ]
    checks = [
        RequirementCheck(
            feature="ёмкость", verdict="confirmed", mentions=2, quote="«емкость большая»"
        )
    ]
    result = await _run_with_llm(monkeypatch, checks, ["ёмкость"], reviews)
    assert result.requirement_checks[0].verdict == "confirmed"


def test_select_reviews_puts_feature_mentions_after_low_rated() -> None:
    reviews = [
        RawReview(source="kaspi", product_id="1", rating=5, text="отличный напор воды у прибора"),
        RawReview(source="kaspi", product_id="1", rating=5, text="IPX7 держит под водой"),
        RawReview(source="kaspi", product_id="1", rating=2, text="шумит слишком сильно при работе"),
    ]
    ordered = rs._select_reviews(reviews, ["IPX7"])
    assert [r.text[:4] for r in ordered] == ["шуми", "IPX7", "отли"]


def test_select_reviews_without_features_keeps_legacy_order() -> None:
    reviews = [
        RawReview(source="kaspi", product_id="1", rating=5, text="отличный напор воды у прибора"),
        RawReview(source="kaspi", product_id="1", rating=5, text="IPX7 держит под водой"),
    ]
    assert [r.text[:4] for r in rs._select_reviews(reviews)] == ["отли", "IPX7"]


def test_too_short_quote_is_not_trusted() -> None:
    reviews = [RawReview(source="kaspi", product_id="1", text="Да, работает тихо и хорошо")]
    check = RequirementCheck(feature="тихий", verdict="confirmed", mentions=3, quote="работает")
    assert rs.verify_checks([check], reviews, ["тихий"])[0].verdict == "insufficient"


# --- Jev overrides the ranking signals (sonnet stays for text + fallback) -----


def _r(text: str, rating: float | None = 5) -> RawReview:
    return RawReview(source="kaspi", product_id="1", text=text, rating=rating)


_ANC_REVIEWS = [
    _r("Шумоподавление никакое, в метро всё слышно.", 2),
    _r("Шумоподавление работает плохо, разочарован.", 2),
    _r("Звук нормальный, за свои деньги ок.", 4),
    _r("Удобно сидят в ушах, лёгкие и приятные.", 4),
    _r("Батареи хватает на целый день работы.", 5),
]


def _sonnet_says_all_clear():
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return ReviewSummary(
            pros=["звук"],
            verdict_line="ок",
            counterfeit_suspicion=False,
            reliability_concern="none",
            requirement_checks=[RequirementCheck(feature="шумоподавление", verdict="confirmed")],
        )

    return _fake_generate


def test_jev_sentences_are_bare_review_text_naming_the_feature() -> None:
    state, questions, sentences = rs.build_questions(_ANC_REVIEWS, ["шумоподавление"])
    assert [s for _, _, s in sentences] == [
        "Шумоподавление никакое, в метро всё слышно",
        "Шумоподавление работает плохо, разочарован",
    ]
    assert state["reviews"][0].startswith("[2★] ")  # stars stay in the review list
    assert set(questions) == {"counterfeit", "reliability", "s0", "s1"}


async def test_jev_answers_override_sonnet_signals(monkeypatch) -> None:
    async def _jev(state, questions):
        refute = {"type": "choice", "choice": "refutes", "probabilities": {"refutes": 0.99}}
        return {
            "counterfeit": {"type": "noul", "noul": 0.97},
            "reliability": {"type": "score", "score": 1.9},
            "s0": refute,
            "s1": refute,
        }

    monkeypatch.setattr(rs, "generate", _sonnet_says_all_clear())
    monkeypatch.setattr(rs, "system_one", _jev)

    summary = await summarize_reviews(_ANC_REVIEWS, ["шумоподавление"])

    assert summary.pros == ["звук"]  # text is still sonnet's
    assert summary.counterfeit_suspicion is True
    assert summary.reliability_concern == "serious"
    (check,) = summary.requirement_checks
    assert (check.verdict, check.mentions) == ("refuted", 2)
    assert check.quote == "Шумоподавление никакое, в метро всё слышно"


async def test_jev_unsure_sentences_leave_feature_insufficient(monkeypatch) -> None:
    async def _jev(state, questions):
        unsure = {"type": "choice", "choice": "refutes", "probabilities": {"refutes": 0.4}}
        return {
            "counterfeit": {"type": "noul", "noul": 0.1},
            "reliability": {"type": "score", "score": 0.2},
            "s0": unsure,
            "s1": unsure,
        }

    monkeypatch.setattr(rs, "generate", _sonnet_says_all_clear())
    monkeypatch.setattr(rs, "system_one", _jev)

    summary = await summarize_reviews(_ANC_REVIEWS, ["шумоподавление"])

    assert summary.requirement_checks[0].verdict == "insufficient"


async def test_jev_failure_keeps_sonnet_signals(monkeypatch) -> None:
    async def _boom(state, questions):
        raise rs.LlmError("gateway down")

    async def _sonnet(prompt, response_model, *, model=None, **kwargs):
        return ReviewSummary(counterfeit_suspicion=True, reliability_concern="minor")

    monkeypatch.setattr(rs, "generate", _sonnet)
    monkeypatch.setattr(rs, "system_one", _boom)

    summary = await summarize_reviews(_ANC_REVIEWS)

    assert summary.counterfeit_suspicion is True
    assert summary.reliability_concern == "minor"
