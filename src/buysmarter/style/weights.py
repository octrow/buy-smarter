"""The model files: Marqo-FashionSigLIP's own int8 ONNX export and a clothes segmenter, pinned.

The spike (docs/spikes/clothing-taste-spike.md) measured int8 against fp32: cosine 0.988,
top-10 overlap 8-9 of 10, 82 ms a photo. The files go to the user cache directory under
the pinned revision; each is checked against its SHA-256 before it is kept.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx
from platformdirs import user_cache_dir

REPO = "Marqo/marqo-fashionSigLIP"
REVISION = "c56244cc94f92419e8369fa71efdaf403b124ce8"
_CHUNK = 1 << 20
_TIMEOUT = httpx.Timeout(30.0, read=120.0)


@dataclass(frozen=True)
class WeightFile:
    path: str  # inside the model repo
    sha256: str
    repo: str = REPO
    revision: str = REVISION
    local: str = ""  # its name under ``weights_dir()``; the repo path when empty

    @property
    def target(self) -> str:
        return self.local or self.path


FILES = {
    "vision": WeightFile(
        "onnx/vision_model_int8.onnx",
        "dae6e934667af50590c3d0ca69c7b14edc71f4df1b4f670e5ba6bc623495b691",
    ),
    "text": WeightFile(
        "onnx/text_model_int8.onnx",
        "f5a7995e029c6ee9346fa46857661c4a171d28b164fa7703b85a527d73adf170",
    ),
    "tokenizer": WeightFile(
        "tokenizer.json",
        "5bc9b16db0e7adf5c9a2bd57f8f9c8e6ebdaabe75679cc956d007f78036d5b4a",
    ),
    # Clothes segmentation (SegFormer-B2, 18 labels, 105 MB): cuts the shoes out of a sample.
    "segmenter": WeightFile(
        "onnx/model.onnx",
        "a93a8dac171b5c1fcc53632a8bfc180bfd9759ea69a3e207451bb07f76add54f",
        repo="mattmdjaga/segformer_b2_clothes",
        revision="584abc1e1d260e23c0fc627c5217a09b2b461046",
        local="segformer_b2_clothes.onnx",
    ),
}


class WeightsError(RuntimeError):
    """A model file could not be downloaded or did not match its hash."""


def weights_dir() -> Path:
    return Path(user_cache_dir("buysmarter")) / "style" / REVISION[:12]


def missing() -> list[str]:
    """The model files not downloaded yet, by name."""
    return [name for name, f in FILES.items() if not (weights_dir() / f.target).is_file()]


async def download(client: httpx.AsyncClient | None = None) -> Path:
    """Download the missing files (about 300 MB the first time); the directory holding them."""
    directory = weights_dir()
    if not missing():
        return directory
    if client is None:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as own:
            return await download(own)
    for name in missing():
        await _fetch(client, FILES[name], directory / FILES[name].target)
    return directory


async def _fetch(client: httpx.AsyncClient, file: WeightFile, target: Path) -> None:
    """Stream one file to ``<target>.part``, hash it on the way, keep it only if it matches."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    url = f"https://huggingface.co/{file.repo}/resolve/{file.revision}/{file.path}"
    try:
        sha256 = await _stream(client, url, part)
    except httpx.HTTPError as exc:
        part.unlink(missing_ok=True)
        raise WeightsError(f"{file.path}: download failed ({exc})") from exc
    if sha256 != file.sha256:
        part.unlink(missing_ok=True)
        raise WeightsError(f"{file.path}: SHA-256 does not match the pinned one")
    part.replace(target)


async def _stream(client: httpx.AsyncClient, url: str, part: Path) -> str:
    """Write the response body to ``part``; its SHA-256."""
    digest = hashlib.sha256()
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        with part.open("wb") as out:
            async for chunk in response.aiter_bytes(_CHUNK):
                digest.update(chunk)
                out.write(chunk)
    return digest.hexdigest()
