"""A clothing fit word ("relaxed", "оверсайз") is a wish to rank by, never a reason to drop a listing."""

import pytest

from buysmarter.ranking.filters import apply_hard_filters, is_fit_wish
from buysmarter.ranking.models import Candidate, QueryConstraints


def _hoodie(title: str) -> Candidate:
    return Candidate(
        source="kaspi", source_product_id=title, title=title, price=10_000, specs={"Цвет": "серый"}
    )


@pytest.mark.parametrize(
    "word", ["relaxed", "Oversize", "оверсайз", "slim fit", "свободного кроя", "regular fit"]
)
def test_fit_words_are_wishes(word):
    assert is_fit_wish(word)


@pytest.mark.parametrize("word", ["IPX7", "капюшон", "хлопок 100%", "на молнии"])
def test_other_features_stay_requirements(word):
    assert not is_fit_wish(word)


def test_a_hoodie_without_the_fit_word_is_kept_while_a_real_requirement_still_filters():
    plain, zipped = _hoodie("Худи Uniqlo"), _hoodie("Худи на молнии")
    relaxed = QueryConstraints(product_type="худи", required_features=["relaxed"])
    zip_only = QueryConstraints(product_type="худи", required_features=["на молнии"])

    kept, rejected = apply_hard_filters([plain, zipped], relaxed)
    assert (len(kept), rejected) == (2, [])
    kept, _ = apply_hard_filters([plain, zipped], zip_only)
    assert kept == [zipped]
