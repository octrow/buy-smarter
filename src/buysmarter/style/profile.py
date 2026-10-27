"""The clothing-taste profile: brands, sample photos and phrases (plan В3, В7, В9, В10).

Brands and phrases live in the ``style`` preference; a sample photo is a clean copy under
``samples_dir()`` (EXIF dropped, at most 1024 px) whose vector is in ``style_vectors`` under
``like`` or ``dislike``, keyed by the copy's name. Brands and phrases need only the core;
adding photos needs the ``style`` extra. The photos never leave the computer: the LLM sees
only the text of a phrase.
"""

import asyncio
import hashlib
import io
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir
from pydantic import BaseModel, Field

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.llm.adapter import LlmError, generate
from buysmarter.memory import prefs
from buysmarter.memory.prefs import as_list

STYLE = "style"  # the preference key
LIKE, DISLIKE = "like", "dislike"  # photo vector kinds
_BRANDS, _LIKES, _NEVERS = "brands", "like", "never"
_MAX_SIDE = 1024
PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})  # what a folder adds
TRANSLATE_MODEL = "free-stack"  # a short mechanical rewrite: the cheap combo

Encode = Callable[[list[bytes]], Any]


class Untranslated(Exception):
    """A non-English phrase and no LLM to translate it."""


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

    @property
    def empty(self) -> bool:
        return not (self.brands or self.likes or self.nevers or self.photos)


@dataclass(frozen=True)
class Added:
    photos: list[Photo]
    skipped: list[str]  # file names that are not photos


def samples_dir() -> Path:
    """The copies of the sample photos (``$BUYSMARTER_STYLE_DIR`` overrides)."""
    override = os.environ.get("BUYSMARTER_STYLE_DIR")
    return Path(override) if override else Path(user_data_dir("buysmarter")) / "style"


async def load() -> Profile:
    stored = await _stored()
    async with async_session() as session:
        rows = await repo.list_style_vectors(session, (LIKE, DISLIKE))
    return Profile(
        brands=as_list(stored.get(_BRANDS)),
        likes=_phrases(stored.get(_LIKES)),
        nevers=_phrases(stored.get(_NEVERS)),
        photos=[Photo(key, label or key, kind == DISLIKE) for kind, key, label in rows],
    )


async def set_brands(brands: list[str]) -> None:
    await _save({**await _stored(), _BRANDS: brands})


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
        dropped = await repo.delete_style_vectors(session, (LIKE, DISLIKE), item)
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


async def add_photos(paths: list[Path], disliked: bool) -> Added:
    """Copy the photos (and the photos in folders) in and store their vectors; needs the extra."""
    copies, skipped = await asyncio.to_thread(_clean_copies, paths)
    if copies:
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
    return Added([Photo(n, label, disliked) for n, (label, _) in copies.items()], skipped)


async def photo_encoder() -> Encode:
    """The real photo encoder; downloads the model the first time (~200 MB)."""
    from buysmarter.style import weights
    from buysmarter.style.encoder import Encoder

    directory = await weights.download()
    return (await asyncio.to_thread(Encoder.load, directory)).images


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
    """A content-named JPEG of the photo, upright, without EXIF or other metadata."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    raw = path.read_bytes()
    try:
        with Image.open(io.BytesIO(raw)) as img:
            rgb = ImageOps.exif_transpose(img).convert("RGB")
    except UnidentifiedImageError as exc:
        raise OSError(f"{path.name}: not an image") from exc
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
