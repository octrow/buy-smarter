"""Authenticity level from the existing counterfeit signals and the brand's counterfeit risk."""

from buysmarter.knowledge.brands import BrandBook
from buysmarter.ranking.authenticity import Authenticity, authenticity
from buysmarter.ranking.flags import SUSPICIOUS_PRICE_CON
from buysmarter.ranking.models import Candidate, ScoredCandidate


def _scored(cons: list[str] | None = None, **overrides) -> ScoredCandidate:
    fields = dict(source="kaspi", source_product_id="1", title="Худи", price=20_000, brand="Adidas")
    fields.update(overrides)
    return ScoredCandidate(
        candidate=Candidate(**fields), total_score=50, confidence=0.8, axes=[], cons=cons or []
    )


def test_the_brands_own_site_is_certainly_original():
    assert authenticity(_scored(source="official")) is Authenticity.CERTAIN


def test_an_official_seller_is_likely_original():
    assert authenticity(_scored(official_seller=True)) is Authenticity.LIKELY


def test_a_low_risk_brand_without_signals_is_likely_original():
    assert authenticity(_scored(counterfeit_risk="low")) is Authenticity.LIKELY


def test_no_signal_and_no_proof_is_unclear():
    assert authenticity(_scored()) is Authenticity.UNCLEAR
    assert authenticity(_scored(counterfeit_risk="high")) is Authenticity.UNCLEAR


def test_any_counterfeit_signal_is_likely_fake_even_for_a_low_risk_brand():
    assert authenticity(_scored(mimic_brand="Adidas")) is Authenticity.FAKE
    fake = _scored(cons=[SUSPICIOUS_PRICE_CON], counterfeit_risk="low", official_seller=True)
    assert authenticity(fake) is Authenticity.FAKE


def test_the_book_drops_a_risk_value_it_does_not_know():
    book = BrandBook(counterfeit_risk={"Adidas": "high", "Nike": "very high"})

    assert book.counterfeit_risk == {"Adidas": "high"}
