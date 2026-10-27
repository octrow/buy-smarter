"""Card photos are fetched and encoded once; their vectors come back from the store."""

import numpy as np

from buysmarter.style.cache import card_vectors


def _encode(calls: list[int]):
    def encode(blobs: list[bytes]) -> np.ndarray:
        calls.append(len(blobs))
        vectors = np.array([[len(b), 1.0, 0.0] for b in blobs], dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    return encode


async def test_a_photo_is_encoded_once_and_read_back_later() -> None:
    fetched: list[str] = []

    async def fetch(url: str) -> bytes | None:
        fetched.append(url)
        return url.encode()

    calls: list[int] = []
    first = await card_vectors(["https://a/1.jpg", "https://a/22.jpg"], fetch, _encode(calls))
    again = await card_vectors(["https://a/1.jpg", "https://a/333.jpg"], fetch, _encode(calls))

    assert fetched == ["https://a/1.jpg", "https://a/22.jpg", "https://a/333.jpg"]
    assert calls == [2, 1]
    assert np.allclose(again["https://a/1.jpg"], first["https://a/1.jpg"])
    assert again["https://a/1.jpg"].dtype == np.float32


async def test_a_photo_that_cannot_be_fetched_has_no_vector() -> None:
    async def fetch(url: str) -> bytes | None:
        return None if "gone" in url else b"jpeg"

    calls: list[int] = []
    vectors = await card_vectors(["https://a/gone.jpg", "https://a/ok.jpg"], fetch, _encode(calls))

    assert list(vectors) == ["https://a/ok.jpg"]
    assert calls == [1]


async def test_nothing_to_fetch_encodes_nothing() -> None:
    async def fetch(url: str) -> bytes | None:
        raise AssertionError("no photo to fetch")

    calls: list[int] = []
    assert await card_vectors([], fetch, _encode(calls)) == {}
    assert calls == []
