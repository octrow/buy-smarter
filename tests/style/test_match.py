"""Cards against the style samples: the nearest liked and «нет» sample, by photo and by phrase."""

import numpy as np

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.ranking.models import Candidate
from buysmarter.style import match, profile
from buysmarter.style.profile import LIKE_PHOTO, LIKE_TEXT, NEVER_PHOTO, NEVER_TEXT

_PHOTOS = {b"a": [1.0, 0.0, 0.0, 0.0], b"b": [0.0, 1.0, 0.0, 0.0]}
_TEXTS = {"glossy white plastic sole": [0.0, 0.0, 1.0, 0.0], "shell jacket": [0.0, 0.0, 0.0, 1.0]}


class FakeEncoder:
    def __init__(self) -> None:
        self.texts_seen: list[str] = []

    def images(self, blobs):
        return np.array([_PHOTOS[b] for b in blobs], dtype=np.float32)

    def texts(self, phrases):
        self.texts_seen += phrases
        return np.array([_TEXTS[p] for p in phrases], dtype=np.float32)


async def _fetch(url: str) -> bytes | None:
    return url.rsplit("/", 1)[-1].encode() or None


def _card(pid: str, image: str | None) -> Candidate:
    url = f"https://img.kz/{image}" if image else None
    return Candidate(source="kaspi", source_product_id=pid, title=pid, price=1, image_url=url)


async def _samples() -> profile.Profile:
    async with async_session() as session:
        await repo.add_style_vectors(
            session,
            profile.LIKE,
            {"l.jpg": np.float32([1, 0, 0, 0]).tobytes()},
            {"l.jpg": "look.jpg"},
        )
        await repo.add_style_vectors(
            session,
            profile.DISLIKE,
            {"d.jpg": np.float32([0, 1, 0, 0]).tobytes()},
            {"d.jpg": "bad.jpg"},
        )
    await profile.add_phrase("glossy white plastic sole", never=True)
    await profile.add_phrase("shell jacket", never=False)
    return await profile.load()


async def test_each_card_gets_its_nearest_samples_and_a_card_without_a_photo_none() -> None:
    current = await _samples()
    liked, plastic, blind = _card("1", "a"), _card("2", "b"), _card("3", None)

    await match.match_pool([liked, plastic, blind], current, FakeEncoder(), _fetch)

    assert liked.style_match[LIKE_PHOTO] == (1.0, "look.jpg")
    assert liked.style_match[NEVER_PHOTO][0] == 0.0
    assert plastic.style_match[NEVER_PHOTO] == (1.0, "bad.jpg")
    assert plastic.style_match[NEVER_TEXT] == (0.0, "glossy white plastic sole")
    assert set(liked.style_match) == {LIKE_PHOTO, LIKE_TEXT, NEVER_PHOTO, NEVER_TEXT}
    assert blind.style_match == {}


async def test_phrase_vectors_are_encoded_once_and_kept() -> None:
    current = await _samples()
    encoder = FakeEncoder()

    await match.match_pool([_card("1", "a")], current, encoder, _fetch)
    await match.match_pool([_card("2", "b")], current, encoder, _fetch)

    assert sorted(encoder.texts_seen) == ["glossy white plastic sole", "shell jacket"]


async def test_a_profile_of_brands_only_matches_nothing() -> None:
    await profile.set_brands(["Salomon"])
    card = _card("1", "a")

    await match.match_pool([card], await profile.load(), FakeEncoder(), _fetch)

    assert card.style_match == {}
