"""Heuristic review-trust scoring: cheap fake-review signals, no LLM.

Pure math over a product's reviews → a 0.3-1.0 trust multiplier applied to the
reviews axis (docs/PLAN.md "Credibility-фактор", фаза 4 anti-fake plans). Four
static signals combine multiplicatively (floor 0.3):

  - rating dispersion: >95% five-star with n>20 reads as inflated
  - date clustering: >50% of reviews inside any 14-day window (needs dates)
  - text quality: >70% of reviews empty or <15 chars
  - junk text: >50% of reviews fail ``is_junk`` (empty, "not used yet",
    logistics only, low-info, rating-only)
  - unverified purchases: >50% of reviews with a known purchase flag are
    not verified purchases (needs at least 5 such reviews)

Five-star share prefers the source's full rating distribution when Kaspi
supplies it; the sample is only the fallback.

# ponytail: static thresholds and word lists, the same for every category;
# retune after 50 real runs or at the first miss seen in one.
"""

import re
from collections.abc import Callable
from datetime import datetime, timedelta

from market_sources.base import RawReview, RawReviewStats

# Rating dispersion.
_MIN_RATING_SAMPLE = 20  # need more than this many rated reviews to judge
_FIVE_STAR_SHARE = 0.95
_FIVE_STAR_PENALTY = 0.5

# Unverified purchases.
_MIN_PURCHASE_SAMPLE = 5
_UNVERIFIED_SHARE = 0.5
_UNVERIFIED_PENALTY = 0.7

# Date clustering.
_CLUSTER_WINDOW_DAYS = 14
_CLUSTER_SHARE = 0.5
_CLUSTER_PENALTY = 0.6
_DATE_FORMAT = "%d.%m.%Y"  # Kaspi review date shape, e.g. "13.05.2023"

# Text quality.
_SHORT_TEXT_LEN = 15
_SHORT_TEXT_SHARE = 0.7
_SHORT_TEXT_PENALTY = 0.6

# Junk text. Ported from shop-scout ReviewFilter.php; text is folded ё → е
# before matching, so the patterns are written with е only.
_JUNK_SHARE = 0.5
_JUNK_PENALTY = 0.7
_SHORT_JUNK_LEN = 25
_LOW_INFO_MAX_WORDS = 3
_LOGISTICS_MAX_LEN = 120
_RATING_ONLY_MAX_LEN = 80
_LOGISTIC_RE = re.compile(
    r"курьер|доставк|упаковк|постамат|пвз|выдач|забрал"
    r"|приехал быстро|пришла быстро|пришел быстро",
    re.IGNORECASE,
)
_PRODUCT_SUBSTANCE_RE = re.compile(
    r"сломал|не\s+работает|работает|качеств|брак|звук|шум|тяжел|легк|удобн|неудобн"
    r"|мощност|хватает|не\s+хватает|держит|греется|целый|долго|быстро\s+(сломал|вышел)"
    r"|через\s+\d|месяц|недел|день|год",
    re.IGNORECASE,
)
_NOT_USED_RE = re.compile(
    r"еще\s+не\s+(польз|тестир|провер|распак|вставил|подключ|установил)"
    r"|пока\s+не\s+(польз|тестир|провер|распаков)"
    r"|не\s+успел\s+(провер|польз|тестир)"
    r"|дополн(ю|ит[ьюе]?\b)"
    r"|польз(овать|уюсь|овал)\s*буду\s+(позже|пока\s+не)"
    r"|поставил\s+пять\s+звезд\s+(аванс|заранее|за\s+будущ)"
    r"|(за|на)\s+будущ(ее|ую)\s+(покупк|оценк)"
    r"|авансом",
    re.IGNORECASE,
)
_LOW_INFO_WORDS = (
    "норм", "ок", "ok", "хорошо", "отлично", "супер", "класс", "топ", "огонь",
    "спасибо", "благодарю", "доволен", "довольна", "советую", "рекомендую",
    "хорош", "пойдет", "подойдет", "годно", "бомба", "кайф", "вау",
)  # fmt: skip
_RATING_ONLY_RE = re.compile(r"(поставил|ставлю|оцениваю)\s+\d?\s*(звезд|балл|★)", re.IGNORECASE)
_DEFECT_RE = re.compile(
    r"брак|сломал|не\s+работает|вышел\s+из\s+строя|разболтал|рассыпался|дефект"
    r"|некачественн|пален|подделка|не\s+оригинал",
    re.IGNORECASE,
)

# Praise that names a defect only to deny it: "без брака", "не подделка", "не сломался".
_NEGATED_DEFECT_RE = re.compile(
    r"\b(без|нет|ни)\s+((одного|единого)\s+)?(брак|дефект|подделк)\w*|\bне\s+(сломал|подделка|брак)\w*",
    re.IGNORECASE,
)

# Never discount below this — even a very fake-looking listing keeps some signal.
_TRUST_FLOOR = 0.3


def _five_star_share(reviews: list[RawReview], stats: RawReviewStats | None = None) -> float | None:
    """Share of five-star reviews, or ``None`` when the sample is too small.

    The full distribution from ``stats`` wins when it is big enough; the
    sampled reviews are the fallback.
    """
    if stats is not None:
        total = sum(stats.rating_counts.values())
        if total > _MIN_RATING_SAMPLE:
            return stats.rating_counts.get(5, 0) / total
    rated = [r.rating for r in reviews if r.rating is not None]
    if len(rated) <= _MIN_RATING_SAMPLE:
        return None
    return sum(1 for rating in rated if rating >= 5) / len(rated)


def _unverified_purchase_share(reviews: list[RawReview]) -> float | None:
    """Share of unverified purchases among reviews with a known flag, or ``None``."""
    known = [r.purchased for r in reviews if r.purchased is not None]
    if len(known) < _MIN_PURCHASE_SAMPLE:
        return None
    return sum(1 for purchased in known if not purchased) / len(known)


def _parse_dates(reviews: list[RawReview]) -> list[datetime]:
    dates: list[datetime] = []
    for review in reviews:
        if not review.created_at:
            continue
        try:
            dates.append(datetime.strptime(review.created_at, _DATE_FORMAT))
        except ValueError:
            continue
    return dates


def _dates_clustered(reviews: list[RawReview]) -> bool:
    """True when >50% of dated reviews fall inside any 14-day window."""
    dates = sorted(_parse_dates(reviews))
    if len(dates) < 2:
        return False
    window = timedelta(days=_CLUSTER_WINDOW_DAYS)
    densest = max(sum(1 for d in dates if start <= d <= start + window) for start in dates)
    return densest / len(dates) > _CLUSTER_SHARE


def _text_low_quality(reviews: list[RawReview]) -> bool:
    """True when >70% of reviews are empty or shorter than 15 characters."""
    if not reviews:
        return False
    short = sum(1 for r in reviews if len(r.text.strip()) < _SHORT_TEXT_LEN)
    return short / len(reviews) > _SHORT_TEXT_SHARE


def _normalize(text: str) -> str:
    return text.strip().lower().replace("ё", "е")


# Quote matching: edge quotes/punctuation dropped so «IPX7 держит» matches IPX7 держит.
_EDGE_PUNCT = "«»\"'“”„.,;:!?()[]…-–— "
# ponytail: 4-char prefix stems instead of a morphology lib; Russian endings vary. Switch to
# a lemmatizer (pymorphy3) at the first inflected feature word a real review misses.
_STEM_LEN = 4
_MIN_STEM_WORD_LEN = 4
_SHORT_TOKEN_RE = re.compile(r"[a-z0-9-]{2,}")  # nfc, usb, wi-fi, 5g


def normalize_text(text: str) -> str:
    """Lowercase, ё→е, whitespace collapsed, surrounding quotes/punctuation stripped."""
    return " ".join(text.lower().replace("ё", "е").split()).strip(_EDGE_PUNCT)


def feature_stems(feature: str) -> list[str]:
    """4-char prefixes of a feature's words.

    Short Cyrillic words ("до", "с") carry no signal and are skipped; short
    Latin/digit tokens ("nfc", "usb", "5g") are the feature itself, kept whole.
    """
    words = normalize_text(feature).split()
    return [
        w[:_STEM_LEN] if len(w) >= _MIN_STEM_WORD_LEN else w
        for w in words
        if len(w) >= _MIN_STEM_WORD_LEN or _SHORT_TOKEN_RE.fullmatch(w)
    ]


def _is_logistics_only(text: str) -> bool:
    """True when the review talks about delivery and says nothing about the product."""
    return (
        _LOGISTIC_RE.search(text) is not None
        and _PRODUCT_SUBSTANCE_RE.search(text) is None
        and len(text) < _LOGISTICS_MAX_LEN
    )


def _is_low_info(text: str) -> bool:
    """True for a very short bare praise word, or a short fragment of at most 3 words."""
    if len(text) > _SHORT_JUNK_LEN:
        return False
    # "Не работает" is short but is exactly the signal we must keep.
    if _DEFECT_RE.search(text) or _PRODUCT_SUBSTANCE_RE.search(text):
        return False
    bare = text.strip(" \t\n\r.,!?")
    if any(bare == word or bare.startswith(word + " ") for word in _LOW_INFO_WORDS):
        return True
    has_digit = any(ch.isdigit() for ch in text)
    return len(text.split()) <= _LOW_INFO_MAX_WORDS and not has_digit and "," not in text


def is_junk(review: RawReview) -> bool:
    """True when the review carries no usable signal about the product."""
    text = _normalize(review.text)
    if not text:
        return True
    if _NOT_USED_RE.search(text) or _is_logistics_only(text) or _is_low_info(text):
        return True
    return len(text) < _RATING_ONLY_MAX_LEN and _RATING_ONLY_RE.search(text) is not None


def filter_junk(reviews: list[RawReview]) -> tuple[list[RawReview], float]:
    """Return (kept reviews, junk share 0-1); the share is 0.0 for empty input."""
    if not reviews:
        return [], 0.0
    kept = [review for review in reviews if not is_junk(review)]
    return kept, (len(reviews) - len(kept)) / len(reviews)


def count_defect_complaints(reviews: list[RawReview]) -> int:
    """Number of reviews complaining about a defect, a fake or a non-original item."""
    return sum(1 for review in reviews if _is_defect_complaint(review))


def _is_defect_complaint(review: RawReview) -> bool:
    return is_defect_text(_normalize(_review_body(review)))


def is_defect_text(text: str) -> bool:
    """True when normalized ``text`` complains about a defect (negated praise excluded)."""
    return _DEFECT_RE.search(_NEGATED_DEFECT_RE.sub(" ", text)) is not None


def _review_body(review: RawReview) -> str:
    """Text plus the structured cons field (Kaspi may carry complaints only there)."""
    return f"{review.text} {review.cons or ''}"


Signal = Callable[[list[RawReview], RawReviewStats | None], tuple[float, str] | None]


def _five_star_signal(
    reviews: list[RawReview], stats: RawReviewStats | None
) -> tuple[float, str] | None:
    share = _five_star_share(reviews, stats)
    if share is None or share <= _FIVE_STAR_SHARE:
        return None
    return _FIVE_STAR_PENALTY, f"{share:.0%} five-star"


def _cluster_signal(
    reviews: list[RawReview], _stats: RawReviewStats | None
) -> tuple[float, str] | None:
    return (_CLUSTER_PENALTY, "dates clustered") if _dates_clustered(reviews) else None


def _short_text_signal(
    reviews: list[RawReview], _stats: RawReviewStats | None
) -> tuple[float, str] | None:
    return (_SHORT_TEXT_PENALTY, "mostly empty text") if _text_low_quality(reviews) else None


def _junk_signal(
    reviews: list[RawReview], _stats: RawReviewStats | None
) -> tuple[float, str] | None:
    _, share = filter_junk(reviews)
    return (_JUNK_PENALTY, f"{share:.0%} junk reviews") if share > _JUNK_SHARE else None


# ponytail: has_photos is collected but unused; no photo bonus, trust stays capped at 1.0.
# Add the bonus once 50 real runs show low-trust products carry fewer photo reviews.
def _unverified_signal(
    reviews: list[RawReview], _stats: RawReviewStats | None
) -> tuple[float, str] | None:
    share = _unverified_purchase_share(reviews)
    if share is None or share <= _UNVERIFIED_SHARE:
        return None
    return _UNVERIFIED_PENALTY, f"{share:.0%} unverified purchases"


_SIGNALS: tuple[Signal, ...] = (
    _five_star_signal,
    _cluster_signal,
    _short_text_signal,
    _junk_signal,
    _unverified_signal,
)


def review_trust(
    reviews: list[RawReview], stats: RawReviewStats | None = None
) -> tuple[float, str]:
    """Return a (multiplier, explanation) pair for a product's reviews.

    The multiplier (0.3-1.0) scales the reviews axis; 1.0 means no suspicion.
    Suspicious signals combine multiplicatively and are floored at 0.3.
    """
    if not reviews:
        return 1.0, "no reviews to assess"
    hits = [hit for signal in _SIGNALS if (hit := signal(reviews, stats)) is not None]
    multiplier = 1.0
    for penalty, _reason in hits:
        multiplier *= penalty
    multiplier = max(round(multiplier, 2), _TRUST_FLOOR)
    explanation = "; ".join(reason for _penalty, reason in hits) or "reviews look organic"
    return multiplier, explanation
