"""Seller questions: the topics a Chinese offer leaves open, the message in Chinese and English."""

from buysmarter.knowledge.landed_cost_rules import CARGO, MARKETPLACE
from buysmarter.knowledge.seller_questions import (
    Body,
    Topic,
    seller_message,
    seller_topics,
)
from buysmarter.ranking.landed_cost import LandedCost, MarketRow
from buysmarter.ranking.models import OFFICIAL_SOURCE, Candidate, ScoredCandidate


def _row(source="taobao", currency="CNY", estimated=True, official=False) -> MarketRow:
    offer = Candidate(
        source=source,
        source_product_id="1",
        title="Худи",
        price=300,
        currency=currency,
        official_seller=official,
    )
    scored = ScoredCandidate(candidate=offer, total_score=1, confidence=0.8, axes=[])
    route = CARGO if currency == "CNY" else MARKETPLACE
    cost = LandedCost(route=route, total=1, parts=(), weight_estimated=estimated)
    return MarketRow(offer=scored, cost=cost)


def test_an_offer_in_tenge_needs_no_questions():
    assert seller_topics(_row(source="kaspi", currency="KZT", estimated=False)) == ()


def test_a_chinese_offer_asks_size_weight_and_authenticity_it_does_not_settle():
    assert seller_topics(_row()) == (Topic.SIZE, Topic.WEIGHT, Topic.AUTHENTICITY)


def test_a_known_weight_and_the_brands_own_store_leave_only_the_size():
    row = _row(source=OFFICIAL_SOURCE, estimated=False, official=True)

    assert seller_topics(row) == (Topic.SIZE,)


def test_the_message_carries_the_measurements_in_both_languages():
    body = Body(height_cm=180, weight_kg=75, chest_cm=100)

    zh, en = seller_message((Topic.SIZE, Topic.WEIGHT), body)

    assert "身高180厘米" in zh and "胸围100厘米" in zh and "重量" in zh
    assert "height 180 cm" in en and "chest 100 cm" in en and "weight with packaging" in en
    assert "腰围" not in zh.split("我")[-1]  # an unset measurement is left out, not invented


def test_without_measurements_the_message_leaves_blanks_to_fill():
    zh, en = seller_message((Topic.SIZE,), None)

    assert "身高___厘米" in zh
    assert "height ___ cm" in en


def test_authenticity_asks_for_proof():
    zh, en = seller_message((Topic.AUTHENTICITY,), Body())

    assert "正品" in zh and "genuine" in en
