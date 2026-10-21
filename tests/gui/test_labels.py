"""The core's English report vocabulary, as the GUI shows it in Russian."""

from buysmarter.gui.labels import clean_title, confidence_hint, explain, pro_con
from buysmarter.ranking.models import Candidate


def test_axis_explanations_read_in_russian_and_keep_their_figures():
    assert explain("price 3103 vs pool median 4584 (ratio 0.68)") == (
        "3\xa0103 ₸ при медиане 4\xa0584 ₸ по всем предложениям (×0.68)"
    )
    assert explain("rating 4.9/5 across 693 reviews (discounted: unverified brand)") == (
        "4.9/5, отзывов: 693 (снижено: бренд не проверен)"
    )
    assert explain("693 reviews = 76% of kaspi pool max (909)") == (
        "693 отзывов — 76% от лидера Kaspi (909)"
    )
    assert explain("out of stock") == "нет в наличии"
    assert explain("in stock; доставка завтра") == "в наличии; доставка завтра"


def test_an_unknown_phrase_passes_through_unchanged():
    assert explain("something new from the core") == "something new from the core"


def test_pros_and_cons_in_plain_words():
    assert pro_con("strong price") == "выгодная цена"
    assert pro_con("weak reviews") == "слабые отзывы"
    assert pro_con("неизвестный бренд") == "неизвестный бренд"


def test_a_doubled_first_word_is_dropped_and_the_rest_kept():
    assert clean_title("Кроссовки кроссовки серый, черный") == "Кроссовки серый, черный"
    assert clean_title("Кроссовки IFORA белый") == "Кроссовки IFORA белый"


def test_confidence_hint_names_the_missing_data():
    cand = Candidate(source="kaspi", source_product_id="1", title="x", price=1, rating=4.5)
    hint = confidence_hint(cand)
    assert "число отзывов" in hint and "известный бренд" in hint and "рейтинг" not in hint
