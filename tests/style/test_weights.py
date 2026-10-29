"""The model files are downloaded once, checked against their pinned SHA-256."""

import hashlib

import httpx
import pytest

from buysmarter.style import weights


def _files(contents: dict[str, bytes]) -> dict[str, weights.WeightFile]:
    return {
        name: weights.WeightFile(f"onnx/{name}.bin", hashlib.sha256(data).hexdigest())
        for name, data in contents.items()
    }


@pytest.fixture
def served(tmp_path, monkeypatch):
    contents = {"vision": b"vision weights", "text": b"text weights"}
    monkeypatch.setattr(weights, "FILES", _files(contents))
    monkeypatch.setattr(weights, "weights_dir", lambda: tmp_path / "style")
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        name = request.url.path.rsplit("/", 1)[-1].removesuffix(".bin")
        return httpx.Response(200, content=contents[name])

    return httpx.MockTransport(handler), requests, contents


async def test_missing_files_are_downloaded_once_into_the_cache(served, tmp_path) -> None:
    transport, requests, contents = served
    assert weights.missing() == ["vision", "text"]

    async with httpx.AsyncClient(transport=transport) as client:
        directory = await weights.download(client)
        await weights.download(client)

    assert weights.missing() == []
    assert (directory / "onnx/vision.bin").read_bytes() == contents["vision"]
    assert len(requests) == 2  # the second call found both files
    assert all(f"/resolve/{weights.REVISION}/" in path for path in requests)


async def test_a_file_with_the_wrong_hash_is_not_kept(served, monkeypatch) -> None:
    transport, _requests, _contents = served
    broken = {**weights.FILES, "text": weights.WeightFile("onnx/text.bin", "0" * 64)}
    monkeypatch.setattr(weights, "FILES", broken)

    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(weights.WeightsError, match="onnx/text.bin"):
            await weights.download(client)

    assert weights.missing() == ["text"]
    assert not list(weights.weights_dir().rglob("*.part"))


async def test_a_file_from_another_repo_is_fetched_at_its_revision_and_kept_by_its_name(
    served, monkeypatch
) -> None:
    transport, requests, contents = served
    other = weights.WeightFile(
        "onnx/text.bin",
        hashlib.sha256(contents["text"]).hexdigest(),
        repo="someone/clothes",
        revision="abc123",
        local="clothes.onnx",
    )
    monkeypatch.setattr(weights, "FILES", {"segmenter": other})

    async with httpx.AsyncClient(transport=transport) as client:
        directory = await weights.download(client)

    assert requests == ["/someone/clothes/resolve/abc123/onnx/text.bin"]
    assert (directory / "clothes.onnx").read_bytes() == contents["text"]
    assert weights.missing() == []
