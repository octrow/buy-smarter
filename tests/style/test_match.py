"""Cards against the style samples: the nearest liked and «нет» sample, by photo and by phrase."""

import numpy as np

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.ranking.models import Candidate
from buysmarter.style import match, profile
from buysmarter.style.profile import LIKE_PHOTO, LIKE_TEXT, NEVER_PHOTO, NEVER_TEXT

_PHOTOS = {b"a": [1.0, 0.0, 0.0, 0.0], b"b": [0.0, 1.0, 0.0, 0.0]}
_TEXTS = {"glossy white plastic sole": [0.0, 0.0, 1.0, 0.0], "shell jacket": [0.0, 0.0, 0.0, 1.0]}
# Photo «a» is sneakers or boots, «b» a jacket or a coat: no garment in common.
_GARMENTS = {
    "a photo of sneakers": [1.0, 0.0, 0.0, 0.0],
    "a photo of boots": [0.5, 0.0, 0.0, 0.0],
    "a photo of jacket": [0.0, 1.0, 0.0, 0.0],
    "a photo of coat": [0.0, 0.5, 0.0, 0.0],
}
_OTHER_GARMENT = [0.0, 0.0, 0.0, 0.0]


class FakeEncoder:
    def __init__(self) -> None:
        self.texts_seen: list[str] = []

    def images(self, blobs):
        return np.array([_PHOTOS[b] for b in blobs], dtype=np.float32)

    def texts(self, phrases):
        self.texts_seen += phrases
        rows = [_TEXTS.get(p) or _GARMENTS.get(p, _OTHER_GARMENT) for p in phrases]
        return np.array(rows, dtype=np.float32)


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
    assert plastic.style_match[NEVER_PHOTO] == (1.0, "bad.jpg")
    assert plastic.style_match[NEVER_TEXT] == (0.0, "glossy white plastic sole")
    assert set(liked.style_match) == {LIKE_PHOTO, LIKE_TEXT, NEVER_TEXT}
    assert blind.style_match == {}


async def test_a_photo_sample_of_another_garment_is_not_compared() -> None:
    current = await _samples()  # a liked sneaker photo, a «нет» jacket photo
    sneakers, jacket = _card("1", "a"), _card("2", "b")

    await match.match_pool([sneakers, jacket], current, FakeEncoder(), _fetch)

    assert NEVER_PHOTO not in sneakers.style_match
    assert LIKE_PHOTO not in jacket.style_match
    assert LIKE_TEXT in jacket.style_match  # phrases are not typed: always compared


async def test_phrase_vectors_are_encoded_once_and_kept() -> None:
    current = await _samples()
    encoder = FakeEncoder()

    await match.match_pool([_card("1", "a")], current, encoder, _fetch)
    await match.match_pool([_card("2", "b")], current, encoder, _fetch)

    phrases = [t for t in encoder.texts_seen if not t.startswith("a photo of ")]
    assert sorted(phrases) == ["glossy white plastic sole", "shell jacket"]
    assert len(encoder.texts_seen) == len(set(encoder.texts_seen))  # garments encoded once too


async def test_a_profile_of_brands_only_matches_nothing() -> None:
    await profile.set_brands(["Salomon"])
    card = _card("1", "a")

    await match.match_pool([card], await profile.load(), FakeEncoder(), _fetch)

    assert card.style_match == {}


async def test_a_shoe_card_is_compared_with_the_shoes_cut_out_not_the_whole_photos() -> None:
    async with async_session() as session:
        await repo.add_style_vectors(
            session,
            profile.LIKE,
            {"l.jpg": np.float32([1, 0, 0, 0]).tobytes()},
            {"l.jpg": "look.jpg"},
        )
        await repo.add_style_vectors(
            session, profile.SHOES[profile.LIKE], {"l.jpg": np.float32([0.8, 0, 0, 0.6]).tobytes()}
        )
    sneakers = _card("1", "a")  # sneakers: the whole photo is closer (1.0) than its shoes (0.8)

    await match.match_pool([sneakers], await profile.load(), FakeEncoder(), _fetch)

    assert sneakers.style_match[LIKE_PHOTO] == (0.8, "look.jpg")
