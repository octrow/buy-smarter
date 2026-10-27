"""Each card's photo against the user's samples: the nearest of each kind (``style`` extra).

Liked and disliked photos are compared photo to photo, «нравится»/«нет» phrases through the
text tower (their English descriptions, encoded once and kept as ``text`` vectors). The
capability turns the cosines into the «Стиль» axis; this module only measures.
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
    urls = [c.image_url for c in pool if c.image_url]
    cards = await cache.card_vectors(urls, fetch, model.images)
    for candidate in pool:
        if (vector := cards.get(candidate.image_url or "")) is not None:
            candidate.style_match = {kind: _nearest(vector, s) for kind, s in kinds.items()}


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
    texts = list(dict.fromkeys(en for en, _ in rows))
    async with async_session() as session:
        stored = await repo.get_style_vectors(session, TEXT, texts)
        new = [t for t in texts if t not in stored]
        if new:
            encoded = await asyncio.to_thread(model.texts, new)
            fresh = {t: encoded[i].astype(np.float32).tobytes() for i, t in enumerate(new)}
            await repo.add_style_vectors(session, TEXT, fresh)
            stored = {**stored, **fresh}
    vectors = np.stack([np.frombuffer(stored[en], np.float32) for en, _ in rows])
    return vectors, [text for _, text in rows]


async def fetch_photo(client: httpx.AsyncClient, url: str) -> bytes | None:
    """A card's photo, ``None`` when it cannot be had (the card is then not compared)."""
    try:
        response = await client.get(url, timeout=_FETCH_TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return response.content
