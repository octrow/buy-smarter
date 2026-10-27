"""Card photo vectors, encoded once and kept in the store by photo URL (``style`` extra).

A search sees the same cards again and again; only new photos are fetched and encoded
(the spike: 82 ms a photo, ~17 KB a Kaspi preview).
"""

import asyncio
from collections.abc import Awaitable, Callable

import numpy as np

from buysmarter.db import repo
from buysmarter.db.base import async_session

CARD = "card"
_FETCH_CONCURRENCY = 8

Fetch = Callable[[str], Awaitable[bytes | None]]
Encode = Callable[[list[bytes]], np.ndarray]


async def card_vectors(urls: list[str], fetch: Fetch, encode: Encode) -> dict[str, np.ndarray]:
    """``{url: unit vector}`` for these photos; a photo that cannot be fetched is left out."""
    async with async_session() as session:
        stored = await repo.get_style_vectors(session, CARD, urls)
    vectors = {url: np.frombuffer(blob, dtype=np.float32) for url, blob in stored.items()}
    new = await _encode_new([u for u in dict.fromkeys(urls) if u not in stored], fetch, encode)
    if new:
        async with async_session() as session:
            await repo.add_style_vectors(session, CARD, {u: v.tobytes() for u, v in new.items()})
    return {**vectors, **new}


async def _encode_new(urls: list[str], fetch: Fetch, encode: Encode) -> dict[str, np.ndarray]:
    semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)

    async def one(url: str) -> bytes | None:
        async with semaphore:
            return await fetch(url)

    blobs = await asyncio.gather(*(one(u) for u in urls))
    got = [(u, b) for u, b in zip(urls, blobs, strict=True) if b]
    if not got:
        return {}
    encoded = encode([b for _, b in got])
    return {u: encoded[i].astype(np.float32) for i, (u, _) in enumerate(got)}
