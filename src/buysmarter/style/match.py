"""Each card's photo against the user's samples: the nearest of each kind (``style`` extra).

Liked and disliked photos are compared photo to photo, «нравится»/«нет» phrases through the
text tower (their English descriptions, encoded once and kept as ``text`` vectors). The
capability turns the cosines into the «Стиль» axis; this module only measures.

A photo sample counts only for a card of its own garment: sneaker photos say nothing about
jeans (the spike: other-category samples rank at chance). The garment is read zero-shot by
the text tower: a photo may be any of its ``LIKELIEST`` garments (an outfit shows several, a
card may be misread: sneakers as sandals), a sample counts when it shares one with the card.
Phrases are not typed: always compared.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx
import numpy as np

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.ranking.models import Candidate
from buysmarter.style import cache
from buysmarter.style.profile import (
    LIKE_PHOTO,
    LIKE_TEXT,
    NEVER_PHOTO,
    NEVER_TEXT,
    TEXT,
    Phrase,
    Profile,
)

_FETCH_TIMEOUT = httpx.Timeout(10.0)
GARMENTS = (
    "sneakers", "boots", "dress shoes", "sandals", "jacket", "coat", "trousers", "jeans",
    "shorts", "t-shirt", "shirt", "sweater", "hoodie", "dress", "skirt", "bag", "hat",
)  # fmt: skip
# Held out on 125 liked photos against 211 Kaspi cards: 2 ranks as well as no filter, 1 drops
# coats from 0.71 to 0.59. ponytail: one global k; a garment-cropping model would make it 1.
LIKELIEST = 2

Fetch = Callable[[str], Awaitable[bytes | None]]


class Encodes(Protocol):
    def images(self, blobs: list[bytes]) -> np.ndarray: ...
    def texts(self, phrases: list[str]) -> np.ndarray: ...


# (vectors, one name per row) of one compared kind
Samples = tuple[np.ndarray, list[str]]


async def match_pool(pool: list[Candidate], current: Profile, model: Encodes, fetch: Fetch) -> None:
    """Set ``style_match`` on every card with a photo; a card without one keeps it empty."""
    kinds = await _samples(current, model)
    if not kinds:
        return
    garments = await _text_vectors([f"a photo of {g}" for g in GARMENTS], model)
    sample_garments = {
        kind: _likeliest(vectors, garments)
        for kind, (vectors, _) in kinds.items()
        if kind in (LIKE_PHOTO, NEVER_PHOTO)
    }
    urls = [c.image_url for c in pool if c.image_url]
    cards = await cache.card_vectors(urls, fetch, model.images)
    for candidate in pool:
        if (vector := cards.get(candidate.image_url or "")) is not None:
            card_garments = _likeliest(vector[None, :], garments)[0]
            candidate.style_match = _card_match(vector, card_garments, kinds, sample_garments)


def _card_match(
    card: np.ndarray,
    card_garments: np.ndarray,
    kinds: dict[str, Samples],
    sample_garments: dict[str, np.ndarray],
) -> dict[str, tuple[float, str]]:
    """The nearest sample of each kind; a photo kind sharing no garment with the card is out."""
    match = {}
    for kind, samples in kinds.items():
        typed: Samples | None = samples
        if kind in sample_garments:
            typed = _of_garment(samples, sample_garments[kind], card_garments)
        if typed is not None:
            match[kind] = _nearest(card, typed)
    return match


def _likeliest(vectors: np.ndarray, garments: np.ndarray) -> np.ndarray:
    """Each row's ``LIKELIEST`` garments, as indexes into ``GARMENTS``."""
    return np.argsort(-(vectors @ garments.T), axis=1, kind="stable")[:, :LIKELIEST]


def _of_garment(
    samples: Samples, sample_garments: np.ndarray, card_garments: np.ndarray
) -> Samples | None:
    """The samples sharing a garment with the card; ``None`` when there is none."""
    keep = np.isin(sample_garments, card_garments).any(axis=1)
    if not keep.any():
        return None
    vectors, names = samples
    return vectors[keep], [n for n, k in zip(names, keep, strict=True) if k]


def _nearest(card: np.ndarray, samples: Samples) -> tuple[float, str]:
    vectors, names = samples
    sims = vectors @ card
    best = int(np.argmax(sims))
    return round(float(sims[best]), 4), names[best]


async def _samples(current: Profile, model: Encodes) -> dict[str, Samples]:
    """The sample vectors of each kind the profile has."""
    kinds = await _photo_samples(current)
    for kind, phrases in ((LIKE_TEXT, current.likes), (NEVER_TEXT, current.nevers)):
        if phrases:
            kinds[kind] = await _phrase_vectors(phrases, model)
    return kinds


async def _photo_samples(current: Profile) -> dict[str, Samples]:
    """Liked and disliked photos, named by their original file names."""
    kinds: dict[str, Samples] = {}
    async with async_session() as session:
        for kind in (LIKE_PHOTO, NEVER_PHOTO):
            chosen = [p for p in current.photos if p.disliked == (kind == NEVER_PHOTO)]
            stored = await repo.get_style_vectors(session, kind, [p.name for p in chosen])
            if found := [p for p in chosen if p.name in stored]:
                vectors = np.stack([np.frombuffer(stored[p.name], np.float32) for p in found])
                kinds[kind] = (vectors, [p.label for p in found])
    return kinds


async def _phrase_vectors(phrases: list[Phrase], model: Encodes) -> Samples:
    """One row per English description, named by the phrase as the user wrote it."""
    rows = [(en, p.text) for p in phrases for en in p.en]
    return await _text_vectors([en for en, _ in rows], model), [text for _, text in rows]


async def _text_vectors(texts: list[str], model: Encodes) -> np.ndarray:
    """One row per text, encoded the first time and kept as ``text`` vectors."""
    unique = list(dict.fromkeys(texts))
    async with async_session() as session:
        stored = await repo.get_style_vectors(session, TEXT, unique)
        new = [t for t in unique if t not in stored]
        if new:
            encoded = await asyncio.to_thread(model.texts, new)
            fresh = {t: encoded[i].astype(np.float32).tobytes() for i, t in enumerate(new)}
            await repo.add_style_vectors(session, TEXT, fresh)
            stored = {**stored, **fresh}
    return np.stack([np.frombuffer(stored[t], np.float32) for t in texts])


async def fetch_photo(client: httpx.AsyncClient, url: str) -> bytes | None:
    """A card's photo, ``None`` when it cannot be had (the card is then not compared)."""
    try:
        response = await client.get(url, timeout=_FETCH_TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return response.content
