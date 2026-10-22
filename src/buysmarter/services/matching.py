"""Title matching across sources: Kaspi/Ozon same-product dedupe and OLX used-market listings.

Both sides compare titles by token overlap plus brand and ModelBook canonical
labels; the used-market half also surfaces notable standalone deals.
"""

import logging
import re
from typing import NamedTuple

from pydantic import BaseModel

from buysmarter.i18n import kzt
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.models_book import ModelBook, ModelEntry
from buysmarter.knowledge.store import get_stored_books, merge_model_books, save_knowledge
from buysmarter.llm.canonicalize import CANONICALIZE_MODEL, canonicalize_titles
from buysmarter.ranking.models import Candidate, QueryConstraints, Recommendation, ScoredCandidate
from buysmarter.ranking.used_defects import find_defects

logger = logging.getLogger(__name__)

_LATIN_TOKEN_RE = re.compile(r"^[a-z0-9]+$", re.IGNORECASE)


def strip_latin_tokens(category: str, book: BrandBook | None = None) -> str | None:
    """Drop pure-ASCII-latin/digit jargon tokens ("TWS", "ANC", "5G") from a category.

    Marketplace full-text search ANDs every query term, and listing titles
    rarely carry latin acronym jargon ("Наушники Huawei Freebuds SE 4" has no
    "TWS" anywhere) — a query built from the raw category can return ZERO
    Kaspi/OLX hits even though plenty of matching products exist. The
    stripped variant is the recall-safe fallback query. Deterministic
    token-shape check, no jargon dictionary to maintain.

    ``book``, when given, protects a latin token the user explicitly typed AS
    A BRAND ("Apple" in "наушники Apple") from being stripped as jargon — only
    tokens the book doesn't recognize as a brand/alias are dropped. Without a
    book, every pure-latin/digit token is dropped (the previous, deterministic
    fallback behavior).

    Returns ``None`` when stripping changes nothing (no latin tokens, or every
    latin token is a recognized brand) OR would empty the string out entirely
    (an all-latin, all-unrecognized category, e.g. "iphone case", has nothing
    else to search with).
    """
    tokens = category.split()
    kept = []
    for t in tokens:
        if not _LATIN_TOKEN_RE.match(t):
            kept.append(t)
        elif book is not None and book.resolve(t)[0] is not None:
            kept.append(t)  # a known brand the user explicitly typed — keep it
    if len(kept) == len(tokens):
        return None
    stripped = " ".join(kept)
    return stripped or None


def is_jargon_only(category: str) -> bool:
    """True when EVERY token is pure-latin/digit — stripping would empty the
    category out entirely (``strip_latin_tokens`` returns ``None`` for this
    reason too, but that's indistinguishable from "nothing to strip" by
    return value alone; callers that need to tell the two apart use this)."""
    tokens = category.split()
    return bool(tokens) and all(_LATIN_TOKEN_RE.match(t) for t in tokens)


def title_matches_category(title: str, category: str) -> bool:
    """Loose check that a card is actually about ``category``, not just the brand.

    A brand's own name search ("Oral-B <category>") also surfaces the brand's
    unrelated catalog (e.g. toothbrushes for an irrigator query); Kaspi's
    search is fuzzy, not a filter. Require at least one real category word
    (len > 2, to skip stray short tokens) to show up in the title too.

    Always checked against the FULL original ``category`` (not the
    latin-stripped search-query variant): the stripped words are a subset of
    the full category's words, so a card matching them already matches here
    too (e.g. "TWS наушники" -> "наушники" is one of these words already) —
    no separate stripped-category branch needed.
    """
    lowered = title.lower()
    words = [w for w in category.lower().split() if len(w) > 2]
    return any(w in lowered for w in words)


def matches_category_or_stripped(title: str, category: str, book: BrandBook | None = None) -> bool:
    """``title_matches_category`` against the full category OR its
    latin-jargon-stripped variant.

    A degenerate recall query (a brand-only OLX head-noun search, or a
    jargon-only category) can return listings with NO relation to the actual
    category at all; this is the downstream safety net that rejects them
    (docs/PLAN.md "Ranking-фиксы") — checked against both variants since a
    listing might phrase things using either.
    """
    if title_matches_category(title, category):
        return True
    stripped = strip_latin_tokens(category, book)
    return stripped is not None and title_matches_category(title, stripped)


# Used-market advisory: only surface a used listing meaningfully cheaper than
# the matched new product (docs/INIT_DATA.md "Used Market").
_USED_PRICE_RATIO = 0.7
_USED_CON = "без гарантии/проверки"
_DEFECT_MARK = "⚠"

# Token-overlap matching. # ponytail: token overlap misses reworded titles; move to embeddings
# at the first product a real run lists twice across sources, or two products merged.
_MIN_TOKEN_LEN = 3
_DEDUPE_OVERLAP = 0.6  # kaspi/ozon same-product merge threshold
_USED_MATCH_OVERLAP = 0.4  # OLX-listing ↔ new-product match threshold
# A same-product duplicate priced 2x+ higher is anti-counterfeit evidence on
# its own (docs/PLAN.md "Ranking-фиксы"), regardless of brand tier: the same
# item can't legitimately cost twice as much on another marketplace.
_DUP_GAP_SUSPICIOUS_RATIO = 2.0


class UsedListing(BaseModel):
    """A used-market OLX listing — matched to a recommended new product, OR
    a "notable" standalone tier-A/B-brand deal with no new-product match
    (``standalone=True``, ``notable_used_listings``) — the saving-vs-new
    fields have nothing to compute against in that case, hence the defaults.
    """

    title: str
    price: int
    new_price: int = 0
    saving: int = 0
    matched_title: str = ""
    city: str | None = None
    url: str | None = None
    con: str = _USED_CON
    canonical: str | None = None  # ModelBook label, e.g. "Xiaomi Water Flosser 2 (MEO705)"
    standalone: bool = False  # True: no new-product match, renderers skip the saving math


def _title_tokens(title: str) -> set[str]:
    """Lowercase alphanumeric tokens of a title, dropping short stray words."""
    return {t for t in re.findall(r"[\w]+", title.lower()) if len(t) >= _MIN_TOKEN_LEN}


def _token_overlap(a: str, b: str) -> float:
    """Overlap of two titles = |shared tokens| / |smaller token set|."""
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _same_brand(a: Candidate, b: Candidate) -> bool:
    return bool(a.brand and b.brand and a.brand.lower() == b.brand.lower())


def _canonical_match(a_title: str, b_title: str, model_book: ModelBook | None) -> bool:
    """True when both titles resolve to the same canonical model (strongest signal)."""
    if model_book is None:
        return False
    a_canon = model_book.resolve(a_title)
    return a_canon is not None and a_canon == model_book.resolve(b_title)


def _is_same_product(cand: Candidate, other: Candidate, model_book: ModelBook | None) -> bool:
    """Cross-source identity: canonical model equality first, then brand + overlap."""
    if _canonical_match(cand.title, other.title, model_book):
        return True
    return _same_brand(cand, other) and _token_overlap(cand.title, other.title) >= _DEDUPE_OVERLAP


def _cheaper_of(cand: Candidate, dup: Candidate) -> tuple[Candidate, Candidate]:
    """Pick (cheaper, pricier); a non-positive price never "wins" as cheaper.

    Ties keep ``dup`` as cheaper (matches the pre-existing tie-break so
    inserting order doesn't flip which listing survives).
    """
    if cand.price > 0 and (dup.price <= 0 or cand.price < dup.price):
        return cand, dup
    return dup, cand


class CrossSourceNote(NamedTuple):
    """A cross-source dedupe note: display text + the raw price ratio
    (pricier/cheaper) at the time of THIS merge.

    ``gap`` is independent of ``Candidate.dup_price_gap``: that field only
    populates at ``_DUP_GAP_SUSPICIOUS_RATIO`` (2.0x, the counterfeit scoring
    signal in ``ranking.engine``), while ``apply_cross_notes`` needs the raw
    ratio down to the lower ``_DUP_GAP_WARN_RATIO`` (1.8x) display threshold
    too — a 1.9x gap is display-worthy honesty even though it's not (yet)
    counterfeit-suspicious for scoring.
    """

    text: str
    gap: float | None


def _merge_pair(
    cand: Candidate, dup: Candidate, prior_priciest: Candidate | None
) -> tuple[Candidate, bool, Candidate, float | None]:
    """Merge ``cand`` into its kept duplicate ``dup``.

    Returns the survivor (a copy with ``dup_price_gap`` refreshed when it
    changes), whether the survivor is ``cand`` rather than ``dup``, the
    cluster's priciest loser so far, and the display gap against it.
    """
    cheaper, pricier = _cheaper_of(cand, dup)
    survivor_changed = cheaper is not dup
    if prior_priciest is None or pricier.price > prior_priciest.price:
        prior_priciest = pricier
    display_gap = prior_priciest.price / cheaper.price if cheaper.price > 0 else None
    dup_price_gap = display_gap if (display_gap or 0) >= _DUP_GAP_SUSPICIOUS_RATIO else None
    if cheaper.dup_price_gap != dup_price_gap:
        cheaper = cheaper.model_copy(update={"dup_price_gap": dup_price_gap})
    return cheaper, survivor_changed, prior_priciest, display_gap


def dedupe_cross_source(
    candidates: list[Candidate], model_book: ModelBook | None = None
) -> tuple[list[Candidate], dict[tuple[str, str], CrossSourceNote]]:
    """Merge same-product listings across sources, keeping the cheaper one.

    Two candidates are "the same product" when they resolve to the same
    canonical model (``ModelBook``, strongest signal) or, failing that, share a
    (known) brand and overlap heavily by title. The cheaper survives; a note
    pointing at the PRICIEST listing ever seen in the group is recorded per
    kept candidate; at a large enough gap ``apply_cross_notes`` turns it into a
    warning con (a pricier copy elsewhere is not a pro). Pure — no I/O, and never mutates an input
    ``Candidate``: the surviving cheaper listing is returned as a fresh copy
    (``model_copy``) when a ``dup_price_gap`` needs recording, so the
    originals in ``candidates`` are untouched.

    Order-independent group spread: each merge's pairwise "pricier" loser is
    compared against the highest pairwise loser this cluster has EVER
    produced (seeded empty, updated whenever a new merge's loser outprices
    it) — since the loser side of ``_cheaper_of`` is, by construction, never
    the (possibly still-changing) survivor itself, this stays a genuinely
    OTHER listing even as the survivor's own identity keeps changing across
    merges. A naive single-comparison "this pricier / this cheaper" ratio
    loses the group's real spread the moment the priciest member gets
    evicted from "pricier" by a later, even-cheaper arrival (a 100k/90k/50k
    trio must read the same gap in all 6 arrival orders, not ``None`` in some
    and a real ratio in others). A same-product duplicate priced
    ``_DUP_GAP_SUSPICIOUS_RATIO``x (or more) above the survivor sets
    ``dup_price_gap`` on it, computed fresh from the running max each merge.
    """
    kept: list[Candidate] = []
    # Per-cluster (kept-list index): the highest-priced "pricier" LOSER of any
    # merge this cluster has produced so far — used both for the gap ratio and
    # for the note's "также на <source> за <price>" wording (the group's true
    # priciest OTHER listing, not just this merge's pairwise loser).
    priciest_seen: dict[int, Candidate] = {}
    notes: dict[tuple[str, str], CrossSourceNote] = {}
    for cand in candidates:
        dup_idx = next(
            (i for i, k in enumerate(kept) if _is_same_product(cand, k, model_book)), None
        )
        if dup_idx is None:
            kept.append(cand)
            continue
        dup = kept[dup_idx]
        cheaper, survivor_identity_changed, priciest, display_gap = _merge_pair(
            cand, dup, priciest_seen.get(dup_idx)
        )
        priciest_seen[dup_idx] = priciest
        kept[dup_idx] = cheaper

        if survivor_identity_changed:
            # The survivor's (source, id) identity changed — drop the old
            # survivor's note so no orphaned/stale key lingers in the result.
            notes.pop((dup.source, dup.source_product_id), None)
        notes[(cheaper.source, cheaper.source_product_id)] = CrossSourceNote(
            text=f"также на {priciest.source} за {kzt(priciest.price)}",
            gap=display_gap,
        )
    return kept, notes


def ad_text(c: Candidate) -> str:
    """The title plus the seller's own description: "на запчасти" often sits in the title only."""
    return f"{c.title}\n{c.specs.get('description', '')}"


def _used_con(c: Candidate) -> str:
    """The standing used-market caveat plus every defect the seller admits to."""
    defects = find_defects(ad_text(c))
    if not defects:
        return _USED_CON
    return f"{_USED_CON}; {_DEFECT_MARK} {', '.join(defects)}"


def _brand_matches(ref: Candidate, used: Candidate, book: BrandBook | None) -> bool:
    """Brand gate for used matching: prefer BrandBook.resolve, else candidates."""
    if book is not None:
        ref_brand = book.resolve(ref.title)[0]
        used_brand = book.resolve(used.title)[0]
        if ref_brand and used_brand:
            return ref_brand == used_brand
    return _same_brand(ref, used)


def _used_ref_matches(
    ref: Candidate, u: Candidate, book: BrandBook | None, model_book: ModelBook | None
) -> bool:
    """A new ref matches a used listing: canonical equality first, else brand+overlap."""
    if _canonical_match(ref.title, u.title, model_book):
        return True
    return (
        _brand_matches(ref, u, book) and _token_overlap(ref.title, u.title) >= _USED_MATCH_OVERLAP
    )


def _best_new_match(
    u: Candidate, new_refs: list[Candidate], book: BrandBook | None, model_book: ModelBook | None
) -> Candidate | None:
    """The priciest new reference ``u`` matches while being meaningfully cheaper."""
    matches = [
        ref
        for ref in new_refs
        if ref.price > 0
        and u.price < _USED_PRICE_RATIO * ref.price
        and _used_ref_matches(ref, u, book, model_book)
    ]
    return max(matches, key=lambda r: r.price) if matches else None


def match_used_listings(
    new_refs: list[Candidate],
    used: list[Candidate],
    book: BrandBook | None,
    model_book: ModelBook | None = None,
    display_labels: dict[str, str] | None = None,
) -> list[UsedListing]:
    """Match used OLX candidates to recommended new products, cheaper only.

    A used listing matches a new reference when both resolve to the same
    canonical model (``ModelBook``, strongest signal) or, failing that, share a
    brand and overlap by title. Among matches priced meaningfully lower, the
    priciest new reference wins; the saving vs new is rendered along with the
    resolved canonical label when available. ``display_labels`` (raw OLX title
    -> LLM-suggested "Brand Model" label, from ``canonicalize_titles``) covers
    titles ``ModelBook`` still can't resolve — display only, never a match
    signal, so a brand-new suggestion never gets mislabeled as an existing
    model. Pure function — no I/O.
    """
    display_labels = display_labels or {}
    listings: list[UsedListing] = []
    for u in used:
        if u.price <= 0:
            continue
        best = _best_new_match(u, new_refs, book, model_book)
        if best is None:
            continue
        # Label with the used item's OWN canonical resolution only — not the
        # matched ref's — so a mere brand+overlap match (e.g. a MEO701 paired
        # with a new F300) is not mislabeled as the same model.
        canonical = model_book.describe(u.title) if model_book is not None else None
        canonical = canonical or display_labels.get(u.title)
        listings.append(
            UsedListing(
                title=u.title,
                price=u.price,
                new_price=best.price,
                saving=best.price - u.price,
                matched_title=best.title,
                city=u.specs.get("city"),
                url=u.url,
                con=_used_con(u),
                canonical=canonical,
            )
        )
    return sorted(listings, key=lambda listing: listing.saving, reverse=True)


# Notable-used floor: how many standalone (no new-product match) tier-A/B
# used deals to surface per run — a short, curated list, not a flood.
_NOTABLE_USED_TOP_N = 3
_NOTABLE_USED_TIERS = ("A", "B")
_LATIN_NAME_MIN_LEN = 4  # shorter latin tokens are model codes/sizes, not names


def _notable_tier(
    u: Candidate, book: BrandBook, budget: int, exclude: set[str], category: str
) -> str | None:
    """The A/B tier of a used listing that qualifies as notable, else None."""
    if u.price <= 0 or u.price > budget:
        return None
    if not matches_category_or_stripped(u.title, category, book):
        return None
    if (u.url or u.title) in exclude:
        return None
    _brand, tier = book.resolve(u.title)
    if tier not in _NOTABLE_USED_TIERS or book.mimicry(u.title) is not None:
        return None
    return tier


def notable_used_listings(
    used: list[Candidate],
    book: BrandBook | None,
    budget: int | None,
    exclude: set[str],
    category: str,
) -> list[UsedListing]:
    """Standout used listings independent of any new-product match.

    ``match_used_listings`` only surfaces a used item when it beats a specific
    new reference on price; a genuinely good used deal from a trusted brand
    can otherwise never surface at all when that brand's new listings simply
    didn't make the ranked pool (docs/INIT_DATA.md "б/у может быть лучше" —
    OLX independence, not conditional on the new-market search). A candidate
    qualifies when: it's actually about ``category`` (or its latin-stripped
    variant — a degenerate recall query upstream can otherwise feed in junk);
    its brand resolves to tier A/B; its title doesn't ALSO mimic a different
    known brand (counterfeit gate parity with the main pool); it's priced
    within budget; and it isn't already one of the matched listings
    (``exclude`` — each matched listing's ``url``, falling back to its
    ``title`` when it has none; the caller builds this the same way from
    ``match_used_listings``' output). Sorted tier-A first, then price
    ascending; capped at ``_NOTABLE_USED_TOP_N``. Pure — no I/O.

    ``budget is None`` skips the whole section: without a budget there is no
    sane price ceiling to bound "notable" by, and a used-market floor without
    ANY price sanity check is worse than no section at all.
    # ponytail: no budget -> no standalone used section. Add a price-sanity floor (a share of
    # the new-market median) once no-budget requests reach a third of POST /recommend jobs.
    """
    if book is None or budget is None:
        return []
    candidates = [
        (tier, u) for u in used if (tier := _notable_tier(u, book, budget, exclude, category))
    ]
    candidates.sort(key=lambda pair: (pair[0] != "A", pair[1].price))
    return [
        UsedListing(
            title=u.title,
            price=u.price,
            city=u.specs.get("city"),
            url=u.url,
            con=_used_con(u),
            standalone=True,
        )
        for _tier, u in candidates[:_NOTABLE_USED_TOP_N]
    ]


def unbranded_used_listings(
    used: list[Candidate],
    book: BrandBook | None,
    pick: Candidate,
    exclude: set[str],
    category: str,
    refs: list[Candidate] | None = None,
) -> list[UsedListing]:
    """No-name used listings meaningfully cheaper than a no-name/tier-C pick.

    In mass no-name segments (guitars under 30k) neither brand matching nor the
    A/B notable floor can ever fire, so OLX shows nothing. When the best pick is
    itself unbranded or tier C, a no-name used listing is a fair comparison:
    surface the ones at least ``1 - _USED_PRICE_RATIO`` cheaper, closest to the
    pick's price first (cheapest-first would lead with toys and junk). The
    saving is measured against a ranked new product sharing a latin name token
    with the listing (an untiered "Levinson" on both markets) when ``refs``
    has one, else against the pick. Tier-A/B listings are left to
    ``notable_used_listings``. Pure — no I/O.
    """
    if _brand_tier(pick.title, book) in _NOTABLE_USED_TIERS:
        return []
    pairs = [
        (u, ref)
        for u in used
        if u.price > 0
        and (u.url or u.title) not in exclude
        and _brand_tier(u.title, book) not in _NOTABLE_USED_TIERS
        and matches_category_or_stripped(u.title, category, book)
        and (ref := _unbranded_ref(u, pick, refs or [])) is not None
    ]
    pairs.sort(key=lambda pair: pair[0].price, reverse=True)
    return [
        UsedListing(
            title=u.title,
            price=u.price,
            new_price=ref.price,
            saving=ref.price - u.price,
            matched_title=ref.title,
            city=u.specs.get("city"),
            url=u.url,
            con=_used_con(u),
        )
        for u, ref in pairs[:_NOTABLE_USED_TOP_N]
    ]


def _latin_names(title: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", title.lower()) if len(t) >= _LATIN_NAME_MIN_LEN}


def _unbranded_ref(u: Candidate, pick: Candidate, refs: list[Candidate]) -> Candidate | None:
    """The priciest same-name ref ``u`` is meaningfully cheaper than, else the pick."""
    cheaper = [r for r in refs if u.price <= r.price * _USED_PRICE_RATIO]
    names = _latin_names(u.title)
    same_name = [r for r in cheaper if names & _latin_names(r.title)]
    if same_name:
        return max(same_name, key=lambda r: r.price)
    return pick if u.price <= pick.price * _USED_PRICE_RATIO else None


def _brand_tier(title: str, book: BrandBook | None) -> str | None:
    return book.resolve(title)[1] if book is not None else None


def _unresolved_used_titles(model_book: ModelBook, used: list[Candidate]) -> list[str]:
    """Distinct OLX titles ``ModelBook.resolve`` failed on, in first-seen order."""
    seen: dict[str, None] = {}
    for u in used:
        if model_book.resolve(u.title) is None:
            seen.setdefault(u.title, None)
    return list(seen)


async def _persist_learned_aliases(
    category: str, book: BrandBook | None, updated_model: ModelBook
) -> None:
    """Best-effort persistence of learned aliases — never break the pipeline."""
    try:
        stored_model = (await get_stored_books(category))[1] or ModelBook()
        merged = merge_model_books(stored_model, updated_model)
        await save_knowledge(category, book or BrandBook(), merged, CANONICALIZE_MODEL)
    except Exception as exc:  # noqa: BLE001 - learning is best-effort, never fatal
        logger.warning("failed to persist learned alias(es) for %r (%r)", category, exc)


def _learn_aliases(
    model_book: ModelBook, titles: list[str], canonicals: list[str | None]
) -> tuple[ModelBook | None, dict[str, str]]:
    """Learn each title mapped to an existing model as its alias; label the rest.

    Returns the updated book (``None`` when nothing was learned) and display
    labels for titles mapped to a model the book doesn't have.
    """
    entries_by_canonical: dict[str, ModelEntry] = {e.canonical: e for e in model_book.entries}
    display_labels: dict[str, str] = {}
    learned = False
    for title, canonical in zip(titles, canonicals, strict=True):
        if canonical is None:
            continue
        entry = entries_by_canonical.get(canonical)
        if entry is None:
            display_labels[title] = canonical  # new-model suggestion, display only
            continue
        if title in entry.aliases:
            continue
        new_aliases = [*entry.aliases, title]
        entries_by_canonical[canonical] = entry.model_copy(update={"aliases": new_aliases})
        learned = True
        logger.info("learned alias: %s → %s", title, canonical)
    if not learned:
        return None, display_labels
    return ModelBook(entries=list(entries_by_canonical.values())), display_labels


async def canonicalize_used_titles(
    category: str,
    book: BrandBook | None,
    model_book: ModelBook | None,
    used: list[Candidate],
    use_llm: bool,
) -> tuple[ModelBook | None, dict[str, str]]:
    """LLM-canonicalize OLX titles ``ModelBook.resolve`` missed (docs/PLAN.md phase 4).

    Skips entirely with no knowledge, ``--no-llm``, or nothing unresolved (no
    LLM call in the common case). A title mapped to an EXISTING canonical is
    the strongest signal — its alias is learned into the book (in-memory here,
    best-effort persisted below) so the next run resolves it without any LLM
    call. A brand-new canonical suggestion (not among the book's models) is
    returned for *display only* — it is never added as an alias, so it can
    never be mistaken for a match to a different, existing model.
    """
    if model_book is None or not use_llm:
        return model_book, {}
    titles = _unresolved_used_titles(model_book, used)
    if not titles:
        return model_book, {}

    known_models = [e.canonical for e in model_book.entries]
    canonicals = await canonicalize_titles(category, known_models, titles)

    updated_model_book, display_labels = _learn_aliases(model_book, titles, canonicals)
    if updated_model_book is None:
        return model_book, display_labels
    await _persist_learned_aliases(category, book, updated_model_book)
    return updated_model_book, display_labels


def _reference_candidates(recommendation: Recommendation) -> list[Candidate]:
    """The used-match reference set: every ranked candidate, deduped by identity.

    Used listings are matched against the whole ranked pool (not just best +
    alternatives) so a cheaper used Xiaomi/Revyline surfaces even when its new
    counterpart is a mid-table top candidate rather than a headline pick — this
    is where the ModelBook canonical matching earns its keep.
    """
    scored = [recommendation.best, *recommendation.alternatives.values(), *recommendation.ranked]
    seen: set[tuple[str, str]] = set()
    refs: list[Candidate] = []
    for s in scored:
        key = (s.candidate.source, s.candidate.source_product_id)
        if key in seen:
            continue
        seen.add(key)
        refs.append(s.candidate)
    return refs


# Below _DUP_GAP_SUSPICIOUS_RATIO (2.0x, the counterfeit scoring signal), a
# same-model duplicate priced meaningfully higher elsewhere is still worth an
# honest warning rather than an upbeat "также на ..." pro (docs/PLAN.md
# "Ranking-фиксы") — display-only, no score change (the scoring-side penalty
# already kicks in at the higher ratio).
_DUP_GAP_WARN_RATIO = 1.8


def _dup_gap_warning(pro_text: str, gap: float) -> str:
    """Reframe the plain cross-source pro note as a price-gap warning con."""
    reframed = pro_text.replace("также на", "та же модель на", 1)
    return f"⚠ {reframed} — разрыв цен {gap:.1f}×, проверьте листинг"


def apply_cross_notes(
    recommendation: Recommendation, notes: dict[tuple[str, str], CrossSourceNote]
) -> None:
    """Turn a large cross-source price gap into a warning con; drop a mild one.

    The kept listing is always the cheaper one, so the note only says a pricier copy exists
    elsewhere: not an advantage worth a pro. At/above ``_DUP_GAP_WARN_RATIO`` the gap itself
    is the signal, framed as a warning.
    """
    scored: list[ScoredCandidate] = [recommendation.best, *recommendation.alternatives.values()]
    for s in scored:
        note = notes.get((s.candidate.source, s.candidate.source_product_id))
        if note is not None and note.gap is not None and note.gap >= _DUP_GAP_WARN_RATIO:
            s.cons.append(_dup_gap_warning(note.text, note.gap))


async def used_market(
    recommendation: Recommendation,
    used_candidates: list[Candidate],
    constraints: QueryConstraints,
    book: BrandBook | None,
    model_book: ModelBook | None,
    use_llm: bool,
) -> list[UsedListing]:
    """OLX listings matching the picks, then notable ones within budget."""
    knowledge = (book, model_book)
    refs = _reference_candidates(recommendation)
    pick = recommendation.best.candidate
    return await _used_listings(refs, pick, used_candidates, constraints, knowledge, use_llm)


async def used_market_without_pick(
    refs: list[Candidate],
    used_candidates: list[Candidate],
    constraints: QueryConstraints,
    book: BrandBook | None,
    model_book: ModelBook | None,
    use_llm: bool,
) -> list[UsedListing]:
    """When no new product passed: OLX listings within budget, matched against ``refs``.

    ``refs`` are the new products the hard filters rejected (over budget, say): a used one
    of the same model still shows its saving against them.
    """
    knowledge = (book, model_book)
    listings = await _used_listings(refs, None, used_candidates, constraints, knowledge, use_llm)
    budget = constraints.budget_max
    return [listing for listing in listings if budget is None or listing.price <= budget]


async def _used_listings(
    refs: list[Candidate],
    pick: Candidate | None,
    used_candidates: list[Candidate],
    constraints: QueryConstraints,
    knowledge: tuple[BrandBook | None, ModelBook | None],
    use_llm: bool,
) -> list[UsedListing]:
    """Matched listings, then notable ones; then cheaper no-name ones when there is a pick."""
    book, model_book = knowledge
    model_book, display_labels = await canonicalize_used_titles(
        constraints.category, book, model_book, used_candidates, use_llm
    )
    used_listings = match_used_listings(refs, used_candidates, book, model_book, display_labels)
    matched_keys = {ul.url or ul.title for ul in used_listings}
    listings = used_listings + notable_used_listings(
        used_candidates,
        book,
        constraints.budget_max,
        exclude=matched_keys,
        category=constraints.category,
    )
    if pick is None:
        return listings
    shown = {ul.url or ul.title for ul in listings}
    return listings + unbranded_used_listings(
        used_candidates, book, pick, exclude=shown, category=constraints.category, refs=refs
    )
