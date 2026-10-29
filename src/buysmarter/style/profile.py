"""The clothing-taste profile: brands, sample photos and phrases (plan В3, В7, В9, В10).

Brands and phrases live in the ``style`` preference; a sample photo is a clean copy under
``samples_dir()`` (EXIF dropped, at most 1024 px) whose vector is in ``style_vectors`` under
``like`` or ``dislike``, keyed by the copy's name; its shoes cut out (``style.shoes``) under
``like_shoes``/``dislike_shoes`` by the same name, an empty vector when it shows none.
Brands and phrases need only the core; adding photos needs the ``style`` extra. The photos
never leave the computer: the LLM sees only the text of a phrase.
"""

import asyncio
import functools
import hashlib
import io
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from platformdirs import user_data_dir
from pydantic import BaseModel, Field

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.llm.adapter import LlmError, generate
from buysmarter.memory import prefs
from buysmarter.memory.prefs import as_list

STYLE = "style"  # the preference key
LIKE, DISLIKE = "like", "dislike"  # photo vector kinds
SHOES = {LIKE: "like_shoes", DISLIKE: "dislike_shoes"}  # their shoes cut out, by the same key
TEXT = "text"  # phrase vectors, keyed by the English description
# What a card is compared with (``Candidate.style_match`` keys).
LIKE_PHOTO, LIKE_TEXT, NEVER_PHOTO, NEVER_TEXT = LIKE, "like_text", DISLIKE, "never_text"
STRENGTH_DEFAULT, STRENGTH_MAX = 20, 50  # the «Стиль» axis weight before normalizing (plan В5)
_BRANDS, _LIKES, _NEVERS, _STRENGTH = "brands", "like", "never", "strength"
_MAX_SIDE = 1024
_CARD_TIMEOUT = httpx.Timeout(15.0)
PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})  # what a folder adds
TRANSLATE_MODEL = "free-stack"  # a short mechanical rewrite: the cheap combo

Encode = Callable[[list[bytes]], Any]
Progress = Callable[[int, int], None]  # (photos done, photos in all)
_BATCH = 16  # photos copied and encoded at a time: the progress step and what a cancel keeps


class Untranslated(Exception):
    """A non-English phrase and no LLM to translate it."""


class NoCard(Exception):
    """No saved search found a product at this link."""


class NoCardPhoto(Exception):
    """The product has no main photo, or it could not be downloaded."""


@dataclass(frozen=True)
class Phrase:
    text: str  # as the user wrote it
    en: list[str]  # what the English text tower reads


@dataclass(frozen=True)
class Photo:
    name: str  # the copy's file name, also its vector key
    label: str  # the original file name
    disliked: bool

    @property
    def path(self) -> Path:
        return samples_dir() / self.name


@dataclass(frozen=True)
class Profile:
    brands: list[str] = field(default_factory=list)
    likes: list[Phrase] = field(default_factory=list)
    nevers: list[Phrase] = field(default_factory=list)
    photos: list[Photo] = field(default_factory=list)
    strength: int = STRENGTH_DEFAULT  # 0: the module is off

    @property
    def empty(self) -> bool:
        return not (self.brands or self.likes or self.nevers or self.photos)

    @property
    def samples(self) -> bool:
        """Something to compare a card's photo with (photos or phrases), not just brands."""
        return bool(self.likes or self.nevers or self.photos)


# ponytail: one process-wide copy of the local user's profile, read by the capability's
# synchronous hooks; per-request profiles if the server ever gets per-user style.
_current = Profile()


def current() -> Profile:
    """The profile as last loaded (``load``); empty before the first load."""
    return _current


@dataclass(frozen=True)
class Added:
    photos: list[Photo]
    skipped: list[str]  # file names that are not photos


def samples_dir() -> Path:
    """The copies of the sample photos (``$BUYSMARTER_STYLE_DIR`` overrides)."""
    override = os.environ.get("BUYSMARTER_STYLE_DIR")
    return Path(override) if override else Path(user_data_dir("buysmarter")) / "style"


async def load() -> Profile:
    """Read the profile and keep it as ``current()``."""
    global _current
    stored = await _stored()
    async with async_session() as session:
        rows = await repo.list_style_vectors(session, (LIKE, DISLIKE))
    _current = Profile(
        brands=as_list(stored.get(_BRANDS)),
        likes=_phrases(stored.get(_LIKES)),
        nevers=_phrases(stored.get(_NEVERS)),
        photos=[Photo(key, label or key, kind == DISLIKE) for kind, key, label in rows],
        strength=as_strength(stored.get(_STRENGTH, STRENGTH_DEFAULT)),
    )
    return _current


def as_strength(value: Any) -> int:
    """A stored strength, clamped to 0..``STRENGTH_MAX``; the default when unreadable."""
    try:
        return min(max(int(value), 0), STRENGTH_MAX)
    except (TypeError, ValueError):
        return STRENGTH_DEFAULT


async def set_brands(brands: list[str]) -> None:
    await _save({**await _stored(), _BRANDS: brands})


async def set_strength(strength: int) -> None:
    await _save({**await _stored(), _STRENGTH: as_strength(strength)})


async def add_phrase(text: str, never: bool) -> Phrase:
    """Store a phrase with its English descriptions; ``Untranslated`` if it cannot get them."""
    text = " ".join(text.split())
    phrase = Phrase(text, [text] if text.isascii() else await _translated(text))
    stored = await _stored()
    key = _NEVERS if never else _LIKES
    kept = [p for p in _phrases(stored.get(key)) if p.text != text]
    await _save({**stored, key: [_raw(p) for p in [*kept, phrase]]})
    return phrase


async def remove(item: str) -> bool:
    """Drop a photo (by its name) or a phrase (by its text); whether anything matched."""
    async with async_session() as session:
        dropped = await repo.delete_style_vectors(session, (LIKE, DISLIKE, *SHOES.values()), item)
    if dropped:
        (samples_dir() / item).unlink(missing_ok=True)
        return True
    stored = await _stored()
    lists = {key: _phrases(stored.get(key)) for key in (_LIKES, _NEVERS)}
    kept = {key: [_raw(p) for p in phrases if p.text != item] for key, phrases in lists.items()}
    if sum(map(len, kept.values())) == sum(map(len, lists.values())):
        return False
    await _save({**stored, **kept})
    return True


async def add_photos(paths: list[Path], disliked: bool, progress: Progress | None = None) -> Added:
    """Copy the photos (and the photos in folders) in and store their vectors; needs the extra.

    Batch by batch: ``progress(done, total)`` after each, and a cancel keeps the batches done.
    """
    files = list(_files(paths))
    added: list[Photo] = []
    skipped: list[str] = []
    for start in range(0, len(files), _BATCH):
        batch = files[start : start + _BATCH]
        copies, missed = await asyncio.to_thread(_clean_copies, batch)
        if copies:
            await _keep(copies, disliked)
        added += [Photo(n, label, disliked) for n, (label, _) in copies.items()]
        skipped += missed
        if progress is not None:
            progress(start + len(batch), len(files))
    await crop_shoes()
    return Added(added, skipped)


async def add_linked(url: str, disliked: bool) -> Photo:
    """The main photo of a product a search found (by its link) as a sample; needs the extra."""
    image_url, title = await _card(url)
    return await add_card(image_url, title, disliked)


async def add_card(image_url: str, title: str, disliked: bool) -> Photo:
    """A card's main photo as a sample, named by the product's title (the report's 👍/👎)."""
    raw = await fetch_card_photo(image_url)
    try:
        name, data = await asyncio.to_thread(_clean, raw)
    except OSError as exc:
        raise NoCardPhoto(image_url) from exc
    await _keep({name: (title, data)}, disliked)
    await crop_shoes()
    return Photo(name, title, disliked)


async def fetch_card_photo(url: str) -> bytes:
    """A card's photo; ``NoCardPhoto`` when it cannot be had."""
    try:
        async with httpx.AsyncClient(timeout=_CARD_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise NoCardPhoto(f"{url}: {exc}") from exc
    return response.content


async def _card(url: str) -> tuple[str, str]:
    """``(main photo, title)`` of the saved product at this link (its query tail ignored)."""
    async with async_session() as session:
        product = await repo.find_product_by_url(session, url.split("?")[0].split("#")[0])
    if product is None:
        raise NoCard(url)
    if not product.image_url:
        raise NoCardPhoto(url)
    return product.image_url, product.title


async def _keep(copies: dict[str, tuple[str, bytes]], disliked: bool) -> None:
    """Encode the clean copies, write them to the samples folder, store their vectors."""
    encode = await photo_encoder()
    vectors = await asyncio.to_thread(encode, [data for _, data in copies.values()])
    _write(copies)
    async with async_session() as session:
        await repo.add_style_vectors(
            session,
            DISLIKE if disliked else LIKE,
            {name: v.tobytes() for name, v in zip(copies, vectors, strict=True)},
            labels={name: label for name, (label, _) in copies.items()},
        )


async def download_model() -> None:
    """Download the model files, then cut the shoes out of the photos added before them."""
    from buysmarter.style import weights

    await weights.download()
    await crop_shoes()


async def crop_shoes() -> None:
    """Cut the shoes out of every sample photo not cut yet and store their vectors.

    Once per photo: a photo without shoes is kept as an empty vector, so it is not cut again.
    Nothing without the segmenter (not downloaded yet: ``download_model`` comes back to it).
    """
    crop = await shoe_cropper()
    if crop is None:
        return
    todo = await _uncut()
    for start in range(0, len(todo), _BATCH):
        await _cut_batch(crop, todo[start : start + _BATCH])


async def _cut_batch(crop: Callable[[bytes], bytes | None], batch: list[tuple[str, str]]) -> None:
    pieces = await asyncio.to_thread(lambda: [_cut(crop, key) for _, key in batch])
    found = [p for p in pieces if p]
    encoded = iter(await asyncio.to_thread(await photo_encoder(), found) if found else [])
    rows: dict[str, dict[str, bytes]] = {}
    for (kind, key), piece in zip(batch, pieces, strict=True):
        rows.setdefault(kind, {})[key] = next(encoded).tobytes() if piece else b""
    async with async_session() as session:
        for kind, vectors in rows.items():
            await repo.add_style_vectors(session, kind, vectors)


async def _uncut() -> list[tuple[str, str]]:
    """``(shoes kind, photo name)`` of every sample photo with no cut-out row yet."""
    async with async_session() as session:
        rows = await repo.list_style_vectors(session, (LIKE, DISLIKE, *SHOES.values()))
    done = {(kind, key) for kind, key, _ in rows if kind in SHOES.values()}
    return [(SHOES[k], key) for k, key, _ in rows if k in SHOES and (SHOES[k], key) not in done]


def _cut(crop: Callable[[bytes], bytes | None], name: str) -> bytes | None:
    try:
        return crop((samples_dir() / name).read_bytes())
    except OSError:  # the copy is gone or unreadable: no shoes to compare
        return None


async def shoe_cropper() -> Callable[[bytes], bytes | None] | None:
    """The real shoe cutter; ``None`` while the segmenter is not downloaded."""
    from buysmarter.style import shoes, weights

    if "segmenter" in weights.missing():
        return None
    segmenter = await asyncio.to_thread(shoes.shared)
    return functools.partial(shoes.shoe_crop, labels=segmenter.labels)


async def photo_encoder() -> Encode:
    """The real photo encoder; downloads the model the first time (~300 MB)."""
    from buysmarter.style import weights
    from buysmarter.style.encoder import shared

    await weights.download()
    return (await asyncio.to_thread(shared)).images


class _Translation(BaseModel):
    descriptions: list[str] = Field(min_length=1, max_length=3)


async def translate(text: str) -> list[str]:
    prompt = (
        "A shopper describes clothing they like or never want, in their own language. "
        "Rewrite it as 2-3 short English descriptions of how such an item looks in a "
        "product photo. Name the thing itself (never 'no X' or 'without X'), lowercase, "
        f"no brand names unless given.\n\nPhrase: {text}"
    )
    result = await generate(prompt, _Translation, model=TRANSLATE_MODEL)
    return list(result.descriptions)  # type: ignore[attr-defined]


async def _translated(text: str) -> list[str]:
    try:
        return await translate(text)
    except LlmError as exc:  # LlmConfigError too: no route at all
        raise Untranslated(text) from exc


def _clean_copies(paths: list[Path]) -> tuple[dict[str, tuple[str, bytes]], list[str]]:
    """``{copy name: (original name, JPEG bytes)}`` and the names that are not photos."""
    copies: dict[str, tuple[str, bytes]] = {}
    skipped = []
    for path in _files(paths):
        try:
            name, data = _clean_copy(path)
        except OSError:  # unreadable, or not an image Pillow knows
            skipped.append(path.name)
            continue
        copies[name] = (path.name, data)
    return copies, skipped


def _files(paths: list[Path]) -> Iterator[Path]:
    """The paths, a folder replaced by the photos in it and its subfolders."""
    for path in paths:
        if not path.is_dir():
            yield path
            continue
        found = (f for f in path.rglob("*") if f.suffix.lower() in PHOTO_SUFFIXES and f.is_file())
        yield from sorted(found)


def _clean_copy(path: Path) -> tuple[str, bytes]:
    return _clean(path.read_bytes())


def _clean(raw: bytes) -> tuple[str, bytes]:
    """A content-named JPEG of the photo, upright, without EXIF or other metadata."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.draft("RGB", (_MAX_SIDE, _MAX_SIDE))  # a big JPEG decodes at a fraction
            rgb = ImageOps.exif_transpose(img).convert("RGB")
    except UnidentifiedImageError as exc:
        raise OSError("not an image") from exc
    rgb.thumbnail((_MAX_SIDE, _MAX_SIDE))
    out = io.BytesIO()
    rgb.save(out, "JPEG", quality=90)  # a fresh file: no EXIF unless passed
    return hashlib.sha256(raw).hexdigest()[:16] + ".jpg", out.getvalue()


def _write(copies: dict[str, tuple[str, bytes]]) -> None:
    samples_dir().mkdir(parents=True, exist_ok=True)
    for name, (_, data) in copies.items():
        (samples_dir() / name).write_bytes(data)


async def _stored() -> dict[str, Any]:
    stored = await prefs.get(STYLE)
    return stored if isinstance(stored, dict) else {}


async def _save(value: dict[str, Any]) -> None:
    await prefs.set(STYLE, value)


def _phrases(value: Any) -> list[Phrase]:
    items = value if isinstance(value, list) else []
    return [
        Phrase(str(item["text"]), as_list(item.get("en")))
        for item in items
        if isinstance(item, dict) and item.get("text")
    ]


def _raw(phrase: Phrase) -> dict[str, Any]:
    return {"text": phrase.text, "en": phrase.en}
