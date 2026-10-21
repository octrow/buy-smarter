"""Per-category product-identity knowledge: canonical models + codes + aliases.

One product wears many names, especially on OLX where private sellers write
sloppy titles (docs/PLAN.md "Product identity / ModelBook"):
``MEO703 == F300 == Mijia Electric Teeth Flosser F300 == Xiaomi F300``;
``«Ирригатор xiaomi me 705» == MEO705 == Xiaomi Water Flosser 2``.

A ``ModelBook`` maps a raw title to its canonical product. Matching is two-pass:
first exact **model-code** match (MEO703, F300, RL670, ...) with digit-boundary
guarding so a code is never a prefix of a longer number, then **alias** substring
match. OLX typos ("me 705" for MEO705) are handled ONLY via explicit alias
entries — codes are matched exactly, never fuzzily ("me705" != "meo705").

Static seed for now; phase 4 LLM-canonicalizes unmatched names (Jev + free-stack LLM, cached)
and appends to the book in ``CategoryKnowledge``.
"""

import re

from pydantic import BaseModel, Field


def _normalize(text: str) -> str:
    """Lowercase and drop hyphens/spaces so "RL-670" / "rl 670" / "RL670" align."""
    return re.sub(r"[\s\-]+", "", text.lower())


class ModelEntry(BaseModel):
    """One canonical product: its brand, model codes, and free-text aliases."""

    canonical: str
    brand: str
    aliases: list[str] = Field(default_factory=list)
    codes: list[str] = Field(default_factory=list)
    # Official price of a genuine item of this model in Kazakhstan (KZT), if known:
    # a far cheaper "Air Jordan 4" is a replica even when the brand's floor is low.
    reference_price: int | None = None

    def matches_code(self, normalized_title: str) -> bool:
        """True if any of this entry's codes appears as a digit-bounded token.

        The code must not be extended by an adjacent digit ("RL220" must not
        match inside "RL2200"); a trailing letter is fine so "f300irrigator"
        (from "F300 irrigator") still matches. Codes are matched exactly — no
        fuzzy edit distance — so "me705" never matches "meo705".
        """
        for code in self.codes:
            norm = _normalize(code)
            if not norm:
                continue
            pattern = rf"(?<![0-9]){re.escape(norm)}(?![0-9])"
            if re.search(pattern, normalized_title):
                return True
        return False

    def matches_alias(self, normalized_title: str) -> bool:
        """True if any explicit alias is a substring of the title.

        The canonical name is deliberately NOT matched here (it would false-match
        prefixes like "Revyline RL 220" inside "RL2200"); canonical products are
        reached via their codes or aliases, both of which are boundary-safe.
        """
        return any(n in normalized_title for n in map(_normalize, self.aliases) if n)


class ModelBook(BaseModel):
    """Canonical products + their codes/aliases for one product category."""

    entries: list[ModelEntry] = Field(default_factory=list)

    def resolve(self, title: str) -> str | None:
        """Resolve a raw title to a canonical product name, or ``None``.

        Model-code match wins over alias match (a code is the strongest signal);
        within a pass, the first matching entry wins.
        """
        entry = self.resolve_entry(title)
        return entry.canonical if entry else None

    def resolve_entry(self, title: str) -> ModelEntry | None:
        """Resolve a raw title to its ``ModelEntry`` (codes first, then aliases)."""
        normalized = _normalize(title)
        for entry in self.entries:
            if entry.matches_code(normalized):
                return entry
        for entry in self.entries:
            if entry.matches_alias(normalized):
                return entry
        return None

    def describe(self, title: str) -> str | None:
        """Human label for a resolved title: "Canonical (CODE)" or ``None``."""
        entry = self.resolve_entry(title)
        if entry is None:
            return None
        if entry.codes:
            return f"{entry.canonical} ({entry.codes[0]})"
        return entry.canonical


# Seed book for irrigators/water flossers. Manually curated; extend via LLM
# canonicalization of unmatched OLX titles in phase 4.
IRRIGATOR_MODEL_BOOK = ModelBook(
    entries=[
        ModelEntry(
            canonical="Xiaomi F300",
            brand="Xiaomi",
            codes=["MEO703", "F300"],
            aliases=["mijia electric teeth flosser f300"],
        ),
        ModelEntry(
            canonical="Xiaomi Water Flosser 2",
            brand="Xiaomi",
            codes=["MEO705"],
            aliases=["me 705", "mijia electric flosser 2", "water flosser 2"],
        ),
        ModelEntry(canonical="Revyline RL 220", brand="Revyline", codes=["RL220"]),
        ModelEntry(canonical="Revyline RL 450", brand="Revyline", codes=["RL450"]),
        ModelEntry(canonical="Revyline RL 670", brand="Revyline", codes=["RL670"]),
        ModelEntry(canonical="Revyline RL 800", brand="Revyline", codes=["RL800"]),
        ModelEntry(canonical="B.Well WI-911", brand="B.Well", codes=["WI911"]),
        ModelEntry(canonical="B.Well WI-922", brand="B.Well", codes=["WI922"]),
        ModelEntry(canonical="Waterpik Ion", brand="Waterpik", aliases=["waterpik ion"]),
        ModelEntry(
            canonical="Waterpik Cordless Advanced",
            brand="Waterpik",
            aliases=["cordless advanced"],
        ),
        ModelEntry(canonical="Waterpik Pulse 3100", brand="Waterpik", aliases=["pulse 3100"]),
        ModelEntry(
            canonical="Waterpik Cordless Express",
            brand="Waterpik",
            aliases=["cordless express"],
        ),
    ]
)


def get_model_book(category: str) -> ModelBook | None:
    """Return the ModelBook for a category, or None if we have no knowledge.

    Keyword match only for now; phase 4 stores LLM-grown books per category in
    ``CategoryKnowledge`` (mirrors ``knowledge.brands.get_brand_book``).
    """
    needle = category.lower()
    if any(kw in needle for kw in ("ирригатор", "irrigator", "flosser")):
        return IRRIGATOR_MODEL_BOOK
    return None
