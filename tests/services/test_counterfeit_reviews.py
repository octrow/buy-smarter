"""Counterfeit check over fetched reviews: the negative page, mentions, unloaded reviews."""

from market_sources.base import RawReview

from buysmarter.ranking.flags import COUNTERFEIT_CON
from buysmarter.ranking.models import Candidate, ScoredCandidate
from buysmarter.ranking.selection import assemble_recommendation, is_barred
from buysmarter.services.review_pass import review_pass


def _scored(pid: str, total: float, review_count: int = 300) -> ScoredCandidate:
    return ScoredCandidate(
        candidate=Candidate(
            source="kaspi",
            source_product_id=pid,
            title=f"Кроссовки {pid}",
            brand="Adidas",
            brand_tier="A",
            price=2_695,
            rating=4.9,
            review_count=review_count,
        ),
        total_score=total,
        confidence=0.8,
        axes=[],
    )


def _review(pid: str, text: str, rating: int = 5) -> RawReview:
    return RawReview(source="kaspi", product_id=pid, text=text, rating=rating, author=text[:8])


class _Kaspi:
    """Kaspi double: praise on the default page, complaints on the negative page."""

    name = "kaspi"

    def __init__(self, default: dict[str, list], negative: dict[str, list]) -> None:
        self._default = default
        self._negative = negative

    async def reviews(self, product_id: str, *, negative: bool = False):
        return (self._negative if negative else self._default).get(product_id, [])


async def test_fake_mentions_on_the_negative_page_bar_the_candidate():
    fake = "fake"
    kaspi = _Kaspi(
        default={fake: [_review(fake, f"классные, удобные {i}") for i in range(9)], "ok": []},
        negative={
            fake: [
                _review(fake, "Это реплика, не оригинал", 1),
                _review(fake, "Китайская копия, развалились через месяц", 2),
            ]
        },
    )
    recommendation = assemble_recommendation(
        [_scored(fake, 90.0, review_count=120), _scored("ok", 70.0)], 0
    )

    result, _summary, _note = await review_pass(recommendation, {"kaspi": kaspi}, False, False)

    by_id = {s.candidate.source_product_id: s for s in result.ranked}
    assert COUNTERFEIT_CON in by_id[fake].cons
    assert is_barred(by_id[fake])
    assert result.best.candidate.source_product_id == "ok"


async def test_negated_mentions_do_not_count():
    pid = "p"
    kaspi = _Kaspi(
        default={pid: [_review(pid, "Оригинал, не подделка"), _review(pid, "не копия, супер")]},
        negative={},
    )
    recommendation = assemble_recommendation([_scored(pid, 90.0)], 0)

    result, _summary, _note = await review_pass(recommendation, {"kaspi": kaspi}, False, False)

    assert COUNTERFEIT_CON not in result.best.cons


async def test_reviews_that_did_not_load_are_reported_not_silently_skipped():
    pid = "p"
    kaspi = _Kaspi(default={}, negative={})
    recommendation = assemble_recommendation([_scored(pid, 90.0, review_count=323)], 0)

    result, _summary, _note = await review_pass(recommendation, {"kaspi": kaspi}, False, False)

    assert any("не загрузились" in con for con in result.best.cons)


async def test_two_mentions_do_not_condemn_a_card_with_thousands_of_reviews():
    pid = "hit"
    kaspi = _Kaspi(
        default={pid: [_review(pid, f"супер {i}") for i in range(9)]},
        negative={pid: [_review(pid, "это подделка", 1), _review(pid, "реплика", 1)]},
    )
    recommendation = assemble_recommendation([_scored(pid, 90.0, review_count=3000)], 0)

    result, _summary, _note = await review_pass(recommendation, {"kaspi": kaspi}, False, False)

    assert COUNTERFEIT_CON not in result.best.cons


def test_counterfeit_wording_and_its_negations():
    from buysmarter.ranking.review_trust import is_counterfeit_text

    fakes = [
        "это подделка",
        "китайская копия",
        "паль полная",
        "не оригинальные кроссовки",
        "жалған аяқ киім",
        "копия оригинала",
    ]
    genuine = [
        "не подделка, оригинал",
        "не похоже на подделку",
        "ни разу не заметил подделки",
        "сомневался, думал подделка, но оригинал",
        "в комплекте копия гарантии",
        "не оригинальный дизайн",
        "пальцы жмут",
        "копить деньги",
    ]

    assert all(is_counterfeit_text(t) for t in fakes)
    assert not any(is_counterfeit_text(t) for t in genuine)
