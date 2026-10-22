"""Model-token relevance: does a listing carry the model code the user typed?

Soft signal only (the engine applies a score factor, never a hard reject).
Tokens are compared on compact strings: lowercase, no spaces/hyphens/dots,
so "ipx-7" in a query matches "IPX 7" in a title.
"""

import re

from buysmarter.ranking.models import Candidate

_TOKEN_SPLIT_RE = re.compile(r"[\s,;:!?()\"«»]+")
# ponytail: substring match on the compact haystack, so "a54" also matches "a540".
# Word-boundary matching on the raw text is the upgrade path if that bites.
_IGNORED_CHARS = str.maketrans("", "", " -.")
_LATIN_RE = re.compile(r"[a-z]")


def compact(text: str) -> str:
    return text.lower().translate(_IGNORED_CHARS)


def _is_model_token(token: str) -> bool:
    """A Latin letter plus a digit ("f300", "a54", "ipx7").

    Cyrillic+digit tokens are units or prices ("128гб", "40000тг") and would
    penalize every "128 GB" listing; bare numbers are budgets or quantities.
    """
    # ponytail: digit-only model codes ("300") are dropped with them — add a
    # separate path only if a real query needs one.
    has_digit = any(ch.isdigit() for ch in token)
    return has_digit and _LATIN_RE.search(token) is not None


def extract_model_tokens(query: str) -> list[str]:
    """Distinct compact model codes in the query, in order of appearance."""
    tokens: list[str] = []
    for raw in _TOKEN_SPLIT_RE.split(query.lower()):
        token = compact(raw)
        if token and _is_model_token(token) and token not in tokens:
            tokens.append(token)
    return tokens


def has_model_match(candidate: Candidate, tokens: list[str] | tuple[str, ...]) -> bool:
    """True when at least one query model code appears in title or spec values."""
    haystack = compact(" ".join([candidate.title, *candidate.specs.values()]))
    return any(token in haystack for token in tokens)


# A model variant word right after the model makes it another model ("iPhone 15 Pro").
_VARIANT = r"pro|max|plus|ultra|mini|lite|fe|se|\+"
_RUN_RE = re.compile(r"[^\W\d_]+|\d+")


def names_model(text: str, model: str) -> bool:
    """True when ``text`` names the whole ``model``: "iPhone 15" is in "iPhone15" and in
    "iPhone 15/15 Pro", not in "iPhone 15 Pro" or "iPhone 150"."""
    runs = _RUN_RE.findall(model.lower())
    if not runs:
        return False
    body = r"[\s\-.]*".join(map(re.escape, runs))
    pattern = rf"(?<![^\W_]){body}(?![^\W_]|[\s\-]*(?:{_VARIANT})(?![^\W_]))"
    return re.search(pattern, text.lower()) is not None


def has_whole_model(candidate: Candidate, models: list[str] | tuple[str, ...]) -> bool:
    """True when the title or a spec value names one of ``models`` whole (``names_model``)."""
    text = " ".join([candidate.title, *candidate.specs.values()])
    return any(names_model(text, model) for model in models)
