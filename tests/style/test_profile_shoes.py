"""Sample photos get their shoes cut out once; the cut-outs are samples and go with the photo."""

import asyncio

import numpy as np

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.ranking.models import Candidate
from buysmarter.style import match, profile
from buysmarter.style.profile import LIKE_PHOTO


def _cropper(calls: list[bytes]):
    async def cropper():
        def crop(data: bytes) -> bytes | None:
            calls.append(data)
            return b"shoes" if len(calls) == 1 else None  # the first photo has shoes

        return crop

    return cropper


async def _shoe_rows() -> dict[str, bytes]:
    async with async_session() as session:
        rows = await repo.list_style_vectors(session, (profile.SHOES[profile.LIKE],))
        keys = [key for _, key, _ in rows]
        return await repo.get_style_vectors(session, profile.SHOES[profile.LIKE], keys)


def test_the_shoes_are_cut_once_and_a_photo_without_them_is_marked(
    monkeypatch, photo, encoder
) -> None:
    calls: list[bytes] = []
    monkeypatch.setattr(profile, "shoe_cropper", _cropper(calls))

    added = asyncio.run(profile.add_photos([photo("a.jpg"), photo("b.jpg", "olive")], False))
    asyncio.run(profile.crop_shoes())  # again: nothing left to cut

    rows = asyncio.run(_shoe_rows())
    assert len(calls) == 2
    assert sorted(len(v) > 0 for v in rows.values()) == [False, True]
    assert set(rows) == {p.name for p in added.photos}
    assert len(asyncio.run(profile.load()).photos) == 2  # cut-outs are not listed as photos


def test_removing_a_photo_drops_its_shoes(monkeypatch, photo, encoder) -> None:
    monkeypatch.setattr(profile, "shoe_cropper", _cropper([]))
    (sample,) = asyncio.run(profile.add_photos([photo("a.jpg")], False)).photos

    asyncio.run(profile.remove(sample.name))

    assert asyncio.run(_shoe_rows()) == {}


class _Encoder:
    def images(self, blobs):
        return np.array([[0.0, 1.0]] * len(blobs), dtype=np.float32)

    def texts(self, phrases):
        return np.array([[1.0, 0.0]] * len(phrases), dtype=np.float32)


async def _fetch(url: str) -> bytes:
    return b"card"


def test_a_shoe_cut_out_is_a_liked_sample_named_by_its_photo(monkeypatch) -> None:
    async def setup() -> profile.Profile:
        async with async_session() as session:
            await repo.add_style_vectors(
                session,
                profile.LIKE,
                {"l.jpg": np.float32([1, 0]).tobytes()},
                {"l.jpg": "look.jpg"},
            )
            await repo.add_style_vectors(
                session, profile.SHOES[profile.LIKE], {"l.jpg": np.float32([0, 1]).tobytes()}
            )
        return await profile.load()

    current = asyncio.run(setup())
    card = Candidate(
        source="kaspi", source_product_id="1", title="1", price=1, image_url="https://img/1"
    )

    asyncio.run(match.match_pool([card], current, _Encoder(), _fetch))

    assert card.style_match[LIKE_PHOTO] == (1.0, "look.jpg")  # the shoes, not the outfit
