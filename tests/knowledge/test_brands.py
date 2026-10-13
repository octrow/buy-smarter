"""Tests for brand-trust knowledge: alias resolution + book lookup."""

from buysmarter.knowledge.brands import IRRIGATOR_BRAND_BOOK, BrandBook, get_brand_book

_TWS_BRAND_BOOK = BrandBook(tiers={"JBL": "B"}, aliases={})


def test_alias_mijia_resolves_to_xiaomi_tier_c():
    assert IRRIGATOR_BRAND_BOOK.resolve("Mijia") == ("Xiaomi", "C")


def test_cyrillic_alias_vaterpik_resolves_to_waterpik_tier_a():
    assert IRRIGATOR_BRAND_BOOK.resolve("Ватерпик") == ("Waterpik", "A")


def test_resolve_from_title_string():
    canonical, tier = IRRIGATOR_BRAND_BOOK.resolve("Xiaomi Mijia Electric Flosser F300")
    assert (canonical, tier) == ("Xiaomi", "C")


def test_resolve_from_title_with_leading_brand():
    assert IRRIGATOR_BRAND_BOOK.resolve("Waterpik WP-660 ирригатор") == ("Waterpik", "A")


def test_canonical_name_matches_case_insensitively():
    assert IRRIGATOR_BRAND_BOOK.resolve("waterpik") == ("Waterpik", "A")


def test_unknown_brand_stays_none():
    assert IRRIGATOR_BRAND_BOOK.resolve("KERN ирригатор портативный") == (None, None)


def test_soocas_is_not_registered():
    # Soocas is a separate brand — must not resolve to Xiaomi.
    assert IRRIGATOR_BRAND_BOOK.resolve("Soocas W3") == (None, None)


def test_whole_word_match_only():
    # "Quip" must not be found inside "equipment".
    assert IRRIGATOR_BRAND_BOOK.resolve("dental equipment") == (None, None)


def test_get_brand_book_matches_irrigator_keywords():
    assert get_brand_book("ирригатор портативный") is IRRIGATOR_BRAND_BOOK
    assert get_brand_book("water flosser") is IRRIGATOR_BRAND_BOOK
    assert get_brand_book("irrigator") is IRRIGATOR_BRAND_BOOK


def test_get_brand_book_unknown_category_returns_none():
    assert get_brand_book("зубная щётка") is None


# --- brand-mimicry (counterfeit) detection ----------------------------------


def test_mimicry_detects_spaced_out_jbl_evading_whole_word_match():
    # Counterfeit dodges "\bJBL\b" by inserting a space: "J BL".
    title = "Наушники беспроводные J BL Tws300"
    assert _TWS_BRAND_BOOK.resolve(title) == (None, None)  # genuinely doesn't match
    assert _TWS_BRAND_BOOK.mimicry(title) == "JBL"


def test_genuine_jbl_match_is_not_mimicry():
    title = "JBL Tune 510BT"
    assert _TWS_BRAND_BOOK.resolve(title) == ("JBL", "B")
    assert _TWS_BRAND_BOOK.mimicry(title) is None


def test_unrelated_title_has_no_mimicry():
    assert _TWS_BRAND_BOOK.mimicry("Xiaomi Redmi Buds 4") is None


def test_short_brand_name_not_mimic_matched_inside_random_text():
    # "LG" (2 chars) must not fire mimicry just because it appears as a
    # substring of unrelated words like "beLGorod".
    short_brand_book = BrandBook(tiers={"LG": "A"}, aliases={})
    assert short_brand_book.mimicry("Наушники beLGorod market") is None


# --- span-rule regression: reject mid-token substring matches ---------------


def test_mimicry_rejects_quip_mid_token_inside_equipment():
    # HIGH-1 regression: "quip" sits mid-word inside "equipment" — must not
    # be flagged, matching the resolve() whole-word invariant above.
    quip_book = BrandBook(tiers={"Quip": "C"}, aliases={})
    assert quip_book.mimicry("dental equipment") is None


def test_mimicry_rejects_sonicare_starting_mid_token():
    # "sonicare" starts mid-word inside "ultrasonic" (spans into "are") — the
    # match doesn't start at a run boundary, so it must not be flagged.
    sonicare_book = BrandBook(
        tiers={"Philips Sonicare": "A"}, aliases={"Sonicare": "Philips Sonicare"}
    )
    assert sonicare_book.mimicry("ultrasonic are") is None


def test_mimicry_accessory_bag_for_waterpik_is_a_documented_span_hit():
    # Known behavior, not a bug: "Water pik" is two separate space-delimited
    # tokens, each a run boundary, so the span rule (start/end of run,
    # straddling >= 2 runs) legitimately fires even for an accessory listing
    # ("bag for a Waterpik"), not just counterfeits. Documented here so a
    # future span-rule change doesn't silently alter this.
    waterpik_book = BrandBook(tiers={"Waterpik": "A"}, aliases={})
    assert waterpik_book.mimicry("сумка для Water pik") == "Waterpik"


def test_empty_brand_book_mimicry_is_none():
    assert BrandBook().mimicry("Наушники беспроводные J BL Tws300") is None


# --- Cyrillic homoglyph fold (M1) -------------------------------------------


def test_mimicry_detects_cyrillic_homoglyph_xiaomi():
    # Cyrillic "Х" (U+0425) look-alike for Latin "X" — single token, but the
    # script transition Cyrillic->Latin inside it counts as a run boundary.
    xiaomi_book = BrandBook(tiers={"Xiaomi": "C"}, aliases={})
    title = "Хiaomi Redmi Buds 4"
    assert xiaomi_book.resolve(title) == (None, None)  # genuinely doesn't match
    assert xiaomi_book.mimicry(title) == "Xiaomi"


def test_mimicry_genuine_cyrillic_word_is_not_a_false_positive():
    # A plain, unrelated Cyrillic phrase must not fold into any known brand.
    xiaomi_book = BrandBook(tiers={"Xiaomi": "C"}, aliases={})
    assert xiaomi_book.mimicry("терпение и труд всё перетрут") is None


def test_empty_alias_or_brand_never_matches_everything():
    book = BrandBook(tiers={"JBL": "B", "": "A"}, aliases={"": "JBL", " ": "JBL"})
    assert book.resolve("Some random speaker") == (None, None)
