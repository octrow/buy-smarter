"""Per-category brand-trust knowledge: tiers + aliases.

Brand trust is a leading ranking axis (docs/PLAN.md "Brand knowledge"):
marketplace ratings/review-counts are easily inflated by noname brands, so
who-made-it matters more than the star average. A ``BrandBook`` maps known
brands to trust tiers (A/B/C) and resolves aliases + brand-in-title mentions
to the canonical brand.

Static seed for now; phase 4 generates per-category books via LLM and stores
them in ``CategoryKnowledge``.
"""

import re
from collections.abc import Iterator

from pydantic import BaseModel, Field, PrivateAttr, field_validator

Tier = str  # "A" | "B" | "C"


class BrandBook(BaseModel):
    """Trust tiers + aliases for the brands of one product category."""

    tiers: dict[str, Tier] = Field(default_factory=dict)
    aliases: dict[str, str] = Field(default_factory=dict)
    # Cheapest genuine new item of each canonical brand in this category (KZT), as its
    # official store / authorized retailers in Kazakhstan sell it: the anti-counterfeit
    # price reference. A marketplace may undercut it a little, never by a lot.
    price_floors: dict[str, int] = Field(default_factory=dict)
    # Each canonical brand's own store / official distributors as marketplace sellers.
    official_sellers: dict[str, list[str]] = Field(default_factory=dict)
    # Each canonical brand's official KZ store page listing this category
    # (``services.official_stores`` reads its JSON-LD for prices and products).
    official_stores: dict[str, str] = Field(default_factory=dict)

    @field_validator("official_stores")
    @classmethod
    def _https_stores_only(cls, stores: dict[str, str]) -> dict[str, str]:
        """Store URLs come from LLM output: keep only https links."""
        return {b: u for b, u in stores.items() if u.lower().startswith("https://")}

    @field_validator("aliases")
    @classmethod
    def _drop_short_aliases(cls, aliases: dict[str, str]) -> dict[str, str]:
        """Drop 1-2 letter aliases that read as ordinary words ("Он" -> On, "on"):
        whole-word matching would turn a pronoun in any title into a trusted brand.
        Latin ones with a capital ("LG", "HP", "Mi") are real brand marks and stay."""
        return {a: c for a, c in aliases.items() if not _is_word_like_short(a.strip())}

    # Lazily-built, cached (canonical, stripped+folded needle) index for
    # ``mimicry`` — computed once per instance instead of per call.
    _mimicry_index: list[tuple[str, str]] | None = PrivateAttr(default=None)

    def resolve(self, raw_brand_or_title: str) -> tuple[str | None, Tier | None]:
        """Resolve a brand string (or product title) → (canonical, tier).

        Case-insensitive whole-word match; the brand often appears only in the
        title (e.g. "KERN ирригатор...", "Xiaomi Mijia F300"). Aliases win over
        canonical names. Returns (None, None) when nothing matches.
        """
        haystack = raw_brand_or_title.lower()
        for alias, canonical in self.aliases.items():
            if _contains_word(haystack, alias):
                return canonical, self.tiers.get(canonical)
        for canonical, tier in self.tiers.items():
            if _contains_word(haystack, canonical):
                return canonical, tier
        return None, None

    def title_brand(self, title: str) -> str | None:
        """The known brand a title names as its own, ignoring "для Xiaomi"-style
        compatibility mentions (an accessory or a case is not that brand's product)."""
        return self.resolve(_COMPATIBILITY_RE.sub(" ", title))[0]

    def is_official_seller(self, brand: str | None, seller: str | None) -> bool:
        """True when ``seller`` is one of ``brand``'s own stores / official distributors."""
        if not brand or not seller:
            return False
        # Exact name only: "Nike Outlet KZ" containing "Nike" is how a fake shop names itself.
        name = _seller_key(seller)
        return bool(name) and name in map(_seller_key, self.official_sellers.get(brand, []))

    def _mimicry_candidates(self) -> list[tuple[str, str]]:
        """Cached (canonical, stripped+folded needle) list, longest needle first."""
        if self._mimicry_index is None:
            merged = {**self.aliases, **{name: name for name in self.tiers}}
            candidates = [(canonical, _strip_and_fold(name)) for name, canonical in merged.items()]
            candidates = [c for c in candidates if len(c[1]) >= _MIMICRY_MIN_LEN]
            candidates.sort(key=lambda c: len(c[1]), reverse=True)
            self._mimicry_index = candidates
        return self._mimicry_index

    def mimicry(self, raw_brand_or_title: str) -> str | None:
        """Canonical brand a title *mimics* without genuinely matching it.

        Counterfeits dodge the whole-word check in ``resolve`` by inserting
        separators into the brand name (e.g. "J BL Tws300" for JBL) or by
        swapping in Cyrillic look-alike letters (e.g. "Хiaomi" for "Xiaomi").
        Both tricks are treated uniformly as "evasion boundaries": the text is
        split into runs at every removed (non-word) separator AND at every
        script transition (Cyrillic <-> other), each run getting an
        incrementing index. A candidate brand name (stripped of separators,
        homoglyph-folded, >= ``_MIMICRY_MIN_LEN`` chars to dodge "LG"-style
        false positives) is a mimicry hit only when its match against the
        similarly folded text (a) starts at the first character of a run,
        (b) ends at the last character of a run, and (c) the start/end runs
        differ — i.e. the match straddles a genuine evasion boundary, not a
        substring sitting mid-word ("quip" inside "equipment") or starting
        mid-word ("sonicare" inside "ultrasonic are").

        If ``resolve()`` already found a genuine match, that specific brand is
        excluded from the mimicry scan (but a *different* known brand's name
        appearing elsewhere in the same text can still register as mimicry).
        """
        resolved, _ = self.resolve(raw_brand_or_title)
        stripped_text, position_meta = _build_run_index(raw_brand_or_title)
        if not stripped_text:
            return None
        folded_text = _fold(stripped_text)
        for canonical_name, needle in self._mimicry_candidates():
            if canonical_name == resolved:
                continue
            for start in _find_all_overlapping(folded_text, needle):
                end = start + len(needle) - 1
                start_run, start_is_run_start, _ = position_meta[start]
                end_run, _, end_is_run_end = position_meta[end]
                if start_is_run_start and end_is_run_end and start_run != end_run:
                    return canonical_name
        return None


_MIN_ALIAS_LEN = 2


def _is_word_like_short(alias: str) -> bool:
    """A 1-2 letter alias that is not a Latin brand mark: Cyrillic, or all lowercase."""
    if len(alias) > _MIN_ALIAS_LEN:
        return False
    return not alias.isascii() or alias == alias.lower()


# "для Apple iPhone 15", "на Apple Watch", "совместим с Philips": the device the product
# fits, not its maker. The target is a Latin word and the Latin/digit words after it,
# else one word, so "Крем для лица CeraVe" and "Повербанк на 20000 mAh Xiaomi" keep theirs.
# ponytail: a Cyrillic brand before a Latin model ("для Сяоми Redmi") still reads Redmi;
# strip the whole run when such titles show up.
_LATIN_WORD = r"[\w.+'-]*[a-z0-9][\w.+'-]*"
_COMPATIBILITY_RE = re.compile(
    r"\b(?:для|на|под|к|for|совместим\w*\s+с|compatible\s+with)\s+"
    rf"(?:[a-z][\w.+'-]*(?:\s+{_LATIN_WORD})*|[\w.+-]+)",
    re.IGNORECASE,
)


def _seller_key(name: str) -> str:
    """Lowercase alnum-only form, so "PUMA official" / "Puma-Official" compare equal."""
    return re.sub(r"[^\w]|_", "", name.lower())


def _contains_word(haystack_lower: str, needle: str) -> bool:
    """Whole-word (Unicode-aware) case-insensitive substring check."""
    needle = needle.strip().lower()
    if not needle:
        return False
    pattern = rf"\b{re.escape(needle)}\b"
    return re.search(pattern, haystack_lower) is not None


# Minimum length of a stripped+folded brand name to qualify as a mimicry hit —
# avoids 2-letter brands ("LG") matching inside unrelated words all the time.
_MIMICRY_MIN_LEN = 3

# Cyrillic → Latin confusables fold (docs/PLAN.md "Ranking-фиксы"): a
# counterfeit title can swap in look-alike Cyrillic letters (e.g. "Хiaomi"
# with a Cyrillic "Х") to dodge a plain-ASCII brand check. Folding whole
# genuinely-Cyrillic words produces garbage Latin text, which is harmless —
# matching is a plain substring check over the folded text either way.
_CONFUSABLES = {
    "а": "a",
    "е": "e",
    "о": "o",
    "р": "p",
    "с": "c",
    "х": "x",
    "у": "y",
    "к": "k",
    "в": "b",
    "м": "m",
    "н": "h",
    "т": "t",
    "і": "i",
}

_WORD_CHAR_RE = re.compile(r"[^\W_]", re.UNICODE)
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")


def _is_word_char(ch: str) -> bool:
    return bool(_WORD_CHAR_RE.match(ch))


def _char_script(ch: str) -> str:
    return "cyr" if _CYRILLIC_RE.match(ch) else "other"


def _strip_non_alnum(text: str) -> str:
    """Drop everything but unicode word chars (letters/digits, no underscore)."""
    return re.sub(r"[\W_]", "", text, flags=re.UNICODE)


def _fold(text: str) -> str:
    """Apply the Cyrillic→Latin confusables fold, character by character."""
    return "".join(_CONFUSABLES.get(ch, ch) for ch in text)


def _strip_and_fold(text: str) -> str:
    return _fold(_strip_non_alnum(text.lower()))


def _build_run_index(text: str) -> tuple[str, list[tuple[int, bool, bool]]]:
    """Lowercase + strip separators, tracking per-character run membership.

    A "run" is a maximal stretch of word characters with no removed separator
    and no script transition (Cyrillic <-> non-Cyrillic) inside it — both are
    treated as evasion boundaries a genuine mimicry match must straddle.
    Returns the stripped (unfolded) text plus, per character, its
    ``(run_index, is_run_start, is_run_end)``.
    """
    lowered = text.lower()
    chars: list[str] = []
    run_of_char: list[int] = []
    current_run = -1
    prev_included = False
    prev_script: str | None = None
    for ch in lowered:
        if not _is_word_char(ch):
            prev_included = False
            prev_script = None
            continue
        script = _char_script(ch)
        if not prev_included or script != prev_script:
            current_run += 1
        chars.append(ch)
        run_of_char.append(current_run)
        prev_included = True
        prev_script = script
    n = len(chars)
    meta = [
        (
            run_of_char[i],
            i == 0 or run_of_char[i] != run_of_char[i - 1],
            i == n - 1 or run_of_char[i] != run_of_char[i + 1],
        )
        for i in range(n)
    ]
    return "".join(chars), meta


def _find_all_overlapping(haystack: str, needle: str) -> Iterator[int]:
    """Yield every (possibly overlapping) start index of ``needle`` in ``haystack``."""
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            return
        yield idx
        start = idx + 1


# Seed book for irrigators/water flossers. Sourced from a manual buyer's guide;
# tiers reflect brand trust, not price. Cyrillic aliases where obvious.
IRRIGATOR_BRAND_BOOK = BrandBook(
    tiers={
        "Waterpik": "A",
        "Philips Sonicare": "A",
        "Oral-B": "A",
        "Burst": "B",
        "Panasonic": "B",
        "B.Well": "B",
        "Revyline": "B",
        "Xiaomi": "C",
        "COSLUS": "C",
        "Nicwell": "C",
        "Quip": "C",
    },
    aliases={
        "Ватерпик": "Waterpik",
        "Philips": "Philips Sonicare",
        "Sonicare": "Philips Sonicare",
        "Филипс": "Philips Sonicare",
        "Oral B": "Oral-B",
        "OralB": "Oral-B",
        "Braun Oral-B": "Oral-B",
        "BWell": "B.Well",
        "B Well": "B.Well",
        "Mijia": "Xiaomi",
        "Сяоми": "Xiaomi",
    },
)


def get_brand_book(category: str) -> BrandBook | None:
    """Return the BrandBook for a category, or None if we have no knowledge.

    Keyword match only for now; phase 4 stores LLM-generated books per category
    in ``CategoryKnowledge``.
    """
    needle = category.lower()
    if any(kw in needle for kw in ("ирригатор", "irrigator", "flosser")):
        return IRRIGATOR_BRAND_BOOK
    return None
