"""Who a product is for (men / women / kids) and the clothing size a query asks for.

Marketplace search ignores the audience word: "мужские кроссовки" returns women's
pairs too (docs/review/2026-10-21-2354/img_10-review.md, item 8). The query's
audience becomes a hard filter against each listing's title and category path.
"""

import re

MALE, FEMALE, KIDS = "male", "female", "kids"

_AUDIENCE_RES = {
    MALE: re.compile(r"\bмужск\w*|\bмужчин\w*|\bmen'?s\b|\bfor men\b", re.IGNORECASE),
    FEMALE: re.compile(r"\bженск\w*|\bженщин\w*|\bwomen'?s?\b|\bfor women\b", re.IGNORECASE),
    KIDS: re.compile(
        r"\bдетск\w*|\bдля детей\b|\bмальчик\w*|\bдевоч\w*|\bподростк\w*|\bkids?\b",
        re.IGNORECASE,
    ),
}
_UNISEX_RE = re.compile(r"\bунисекс\b|\bunisex\b", re.IGNORECASE)

# "42 размер", "размер 42.5", "р-р 43", "размер XL" — not "размер 90 х 200" (a mattress)
# or "размер 55 дюймов" (a TV): a dimension after the number means it is not a size.
_NOT_DIMENSION = r"(?!\s*(?:[xх×*]|дюйм|см|мм|\"|'))"
_SIZE_RE = re.compile(
    r"(?:\bразмер\w*|\bр-р|\bр\.)\s*(?P<a>\d{2}(?:[.,]5)?|\d?X{0,3}[SML]|\d?XL)\b"
    + _NOT_DIMENSION
    + r"|\b(?P<b>\d{2}(?:[.,]5)?)\s*(?:размер\w*|р-р)",
    re.IGNORECASE,
)
# Clothing and footwear: the only categories where a size is chosen, not specified.
_APPAREL_RE = re.compile(
    r"обув|кроссов|кед[ыа]?\b|ботин|туфл|сапог|сандал|шлеп|тапоч|мокасин|лофер|слипон"
    r"|куртк|пальто|пуховик|плащ|жилет|футболк|майк|рубаш|блузк|свитер|кофт|худи|толстовк"
    r"|свитшот|брюк|джинс|шорт|юбк|плать|костюм|комбинезон|пижам|носк|колготк|бель[ея]"
    r"|трус|бюстгальт|купальн|перчат|шапк|кепк|бейсболк",
    re.IGNORECASE,
)


def is_apparel(text: str) -> bool:
    """True when ``text`` names clothing or footwear."""
    return _APPAREL_RE.search(text) is not None


def audiences(text: str) -> set[str]:
    """Every audience ``text`` names."""
    return {name for name, pattern in _AUDIENCE_RES.items() if pattern.search(text)}


def query_audience(text: str) -> str | None:
    """The single audience a query asks for, or None when it names none or several."""
    found = audiences(text)
    return found.pop() if len(found) == 1 else None


def as_audience(value: str | None) -> str | None:
    """One of ours from whatever word names it ("мужской" -> MALE), None when it names none.

    The LLM parse fills ``gender`` in its own words; the filter compares names.
    """
    if value is None or value in _AUDIENCE_RES:
        return value
    return query_audience(value)


def other_audience(wanted: str, listing_text: str) -> str | None:
    """The audience a listing is marked for when that excludes ``wanted``, else None.

    A unisex listing, or one naming ``wanted`` among others ("для мужчин и
    женщин"), fits; an unmarked listing fits too (no data is not a mismatch).
    """
    if _UNISEX_RE.search(listing_text):
        return None
    found = audiences(listing_text)
    if not found or wanted in found:
        return None
    return sorted(found)[0]


def query_size(text: str) -> str | None:
    """The clothing/shoe size a query names, normalized ("42,5" -> "42.5", "xl" -> "XL").

    None outside clothing and footwear: "монитор размер 27" names a spec, not a size.
    """
    if not is_apparel(text):
        return None
    match = _SIZE_RE.search(text)
    if match is None:
        return None
    return (match["a"] or match["b"]).replace(",", ".").upper()


def is_size_phrase(text: str) -> bool:
    """True when ``text`` is just a size mention ("42 размер"), not a product feature."""
    match = _SIZE_RE.search(text)
    return match is not None and not _SIZE_RE.sub("", text).strip(" ,.")


def strip_size(text: str) -> str:
    """``text`` without its size mention: marketplace search ANDs "42 размер" away."""
    return " ".join(_SIZE_RE.sub(" ", text).split())


# A bare size token in an apparel phrase ("кроссовки 42", "футболка XL").
_BARE_SIZE_RE = re.compile(r"(?<!\w)(?:\d{2}(?:[.,]5)?|X{1,3}L|XS|[SML])(?!\w)")


def bare_product_type(text: str) -> str:
    """``text`` without audience and size words: the naive product type ("кроссовки")."""
    rest = strip_audience(strip_size(text))
    if is_apparel(text):
        rest = " ".join(_BARE_SIZE_RE.sub(" ", rest).split())
    return rest or text


def strip_audience(text: str) -> str:
    """``text`` without its audience words ("мужские", "для детей")."""
    for pattern in _AUDIENCE_RES.values():
        text = re.sub(rf"(?:\bдля\s+)?(?:{pattern.pattern})", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def strip_named_size(text: str, size: str) -> str:
    """``text`` without ``size`` as a separate token ("мужские кроссовки 42", size "42")."""
    pattern = rf"(?<![\w.]){re.escape(size)}(?![\w]|[.,]\d)"
    return " ".join(re.sub(pattern, " ", text, flags=re.IGNORECASE).split())


def has_size(title: str, size: str) -> bool:
    """True when ``title`` names ``size`` as a separate token ("42" is not in "142")."""
    return (
        re.search(rf"(?<![\w.]){re.escape(size)}(?![\w]|[.,]\d)", title, re.IGNORECASE) is not None
    )
