"""``buysmarter style``: the clothing-taste profile, as the app's «Стиль» screen (plan В9)."""

import asyncio
import io

import pytest
from typer.testing import CliRunner

from buysmarter import style
from buysmarter.cli import main
from buysmarter.cli import style as style_cli
from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.llm.adapter import LlmError
from buysmarter.style import profile

runner = CliRunner()


def _style(*args: str):
    return runner.invoke(main.app, ["style", *args])


def _profile() -> profile.Profile:
    return asyncio.run(profile.load())


def _vectors(kind: str, keys: list[str]) -> dict[str, bytes]:
    async def read():
        async with async_session() as session:
            return await repo.get_style_vectors(session, kind, keys)

    return asyncio.run(read())


@pytest.fixture
def translated(monkeypatch):
    """The LLM turns a Russian phrase into English descriptions; records what it was asked."""
    asked: list[str] = []

    async def fake(text: str) -> list[str]:
        asked.append(text)
        return ["glossy white plastic sole", "cheap shiny white rubber sole"]

    monkeypatch.setattr(profile, "translate", fake)
    return asked


def test_an_empty_profile_shows_every_part_in_russian() -> None:
    result = _style()

    assert result.exit_code == 0
    for label in ("Бренды", "Нравится", "Нет", "Фото"):
        assert label in result.stdout
    assert _profile().empty


def test_brands_are_stored_cleaned_and_shown() -> None:
    result = _style("brands", "Salomon,  New Balance ,")

    assert result.exit_code == 0
    assert _profile().brands == ["Salomon", "New Balance"]
    assert "Salomon, New Balance" in _style().stdout


def test_an_english_phrase_is_kept_as_written_without_the_llm(translated) -> None:
    result = _style("never", "glossy white plastic sole")

    assert result.exit_code == 0
    assert translated == []
    assert _profile().nevers == [
        profile.Phrase("glossy white plastic sole", ["glossy white plastic sole"])
    ]


def test_a_russian_phrase_is_stored_with_its_english_descriptions(translated) -> None:
    result = _style("like-text", "белая подошва из дешёвого пластика")

    assert result.exit_code == 0
    assert translated == ["белая подошва из дешёвого пластика"]
    (phrase,) = _profile().likes
    assert phrase.en == ["glossy white plastic sole", "cheap shiny white rubber sole"]
    assert "glossy white plastic sole" in _style().stdout


def test_a_russian_phrase_without_the_llm_is_refused_with_advice(monkeypatch) -> None:
    async def down(text):
        raise LlmError("no route")

    monkeypatch.setattr(profile, "translate", down)

    result = _style("never", "белая пластиковая подошва")

    assert result.exit_code == 1
    assert style_cli.UNTRANSLATED in result.stdout
    assert _profile().nevers == []


def test_photos_are_copied_without_exif_and_kept_as_samples(photo, encoder) -> None:
    image = pytest.importorskip("PIL.Image")

    result = _style("add", str(photo("look.jpg")), str(photo("bad.jpg", "white")), "--no")
    liked = _style("add", str(photo("good.jpg", "olive")))

    assert result.exit_code == 0 and liked.exit_code == 0
    photos = _profile().photos
    assert sorted((p.label, p.disliked) for p in photos) == [
        ("bad.jpg", True),
        ("good.jpg", False),
        ("look.jpg", True),
    ]
    for p in photos:
        assert p.path.parent == profile.samples_dir()
        with image.open(p.path) as copy:
            assert not copy.getexif()  # the camera tags stay behind
    good = next(p for p in photos if not p.disliked)
    assert _vectors(profile.LIKE, [good.name]).keys() == {good.name}
    assert "1 «нравится», 2 «нет»" in _style().stdout


def test_the_same_photo_twice_is_one_sample(photo, encoder) -> None:
    path = str(photo())

    _style("add", path)
    _style("add", path)

    assert len(_profile().photos) == 1


def test_a_file_that_is_not_a_photo_is_skipped_with_the_reason(tmp_path, photo, encoder) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("not a photo")

    result = _style("add", str(notes), str(photo()))

    assert result.exit_code == 0
    assert "notes.txt" in result.stdout and style_cli.NOT_A_PHOTO in result.stdout
    assert [p.label for p in _profile().photos] == ["look.jpg"]


def test_remove_drops_a_photo_with_its_file_and_vector(photo, encoder) -> None:
    _style("add", str(photo()))
    (sample,) = _profile().photos

    result = _style("remove", sample.name)

    assert result.exit_code == 0
    assert _profile().photos == []
    assert not sample.path.exists()
    assert _vectors(profile.LIKE, [sample.name]) == {}


def test_remove_drops_a_phrase() -> None:
    _style("never", "glossy white plastic sole")

    result = _style("remove", "glossy white plastic sole")

    assert result.exit_code == 0
    assert _profile().nevers == []


def test_removing_what_is_not_there_says_so() -> None:
    result = _style("remove", "nothing")

    assert result.exit_code == 1
    assert style_cli.NOT_FOUND in result.stdout


def test_adding_photos_without_the_extra_says_how_to_install_it(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(style, "available", lambda: False)

    result = _style("add", str(tmp_path / "look.jpg"))

    assert result.exit_code == 1
    assert style_cli.NO_EXTRA in result.stdout


def test_a_folder_is_added_with_its_subfolders(tmp_path, photo, encoder) -> None:
    looks = tmp_path / "looks"
    (looks / "boots").mkdir(parents=True)
    photo("a.jpg").rename(looks / "a.jpg")
    photo("b.jpg", "olive").rename(looks / "boots" / "b.jpg")
    (looks / "notes.txt").write_text("not a photo")  # inside a folder: not a sample, no noise

    result = _style("add", str(looks))

    assert result.exit_code == 0
    assert sorted(p.label for p in _profile().photos) == ["a.jpg", "b.jpg"]
    assert "notes.txt" not in result.stdout


def test_download_fetches_the_model_once(monkeypatch, tmp_path) -> None:
    from buysmarter.style import weights

    calls = []

    async def fake(client=None):
        calls.append(1)
        return tmp_path

    monkeypatch.setattr(weights, "download", fake)

    result = _style("download")

    assert result.exit_code == 0
    assert calls == [1]
    assert style_cli.MODEL_READY in result.stdout


def test_strength_is_stored_and_shown() -> None:
    result = _style("strength", "35")

    assert result.exit_code == 0
    assert _profile().strength == 35
    shown = _style().stdout
    assert style_cli.STRENGTH in shown and style_cli.strength_line(35) in shown


def test_a_strength_out_of_range_is_refused() -> None:
    result = _style("strength", "80")

    assert result.exit_code != 0
    assert _profile().strength == profile.STRENGTH_DEFAULT


_CARD_URL = "https://kaspi.kz/shop/p/salomon-xt-6-123/"


@pytest.fixture
def stored_card(monkeypatch):
    """A product a search saved, and its main photo served without a network."""
    image = pytest.importorskip("PIL.Image")
    from market_sources.base import RawProduct

    raw = RawProduct(
        source="kaspi",
        product_id="123",
        title="Кроссовки Salomon XT-6",
        price=90000,
        url=_CARD_URL,
        image_url="https://img.kaspi.kz/123.jpg",
    )

    async def save():
        async with async_session() as session:
            await repo.upsert_product(session, raw)

    asyncio.run(save())

    async def served(url):
        assert url == raw.image_url
        out = io.BytesIO()
        image.new("RGB", (40, 40), "grey").save(out, "JPEG")
        return out.getvalue()

    monkeypatch.setattr(profile, "fetch_card_photo", served)
    return raw


@pytest.mark.parametrize(("command", "disliked"), [("like", False), ("dislike", True)])
def test_a_found_product_becomes_a_sample_by_its_link(stored_card, encoder, command, disliked):
    result = _style(command, _CARD_URL + "?c=750000000")  # the link as copied, with its tail

    assert result.exit_code == 0
    (sample,) = _profile().photos
    assert (sample.label, sample.disliked) == ("Кроссовки Salomon XT-6", disliked)
    assert sample.path.is_file()


def test_a_link_no_search_saved_is_refused_with_advice(encoder) -> None:
    result = _style("like", "https://kaspi.kz/shop/p/unknown-1/")

    assert result.exit_code == 1
    assert style_cli.CARD_UNKNOWN in result.stdout
    assert _profile().photos == []


def test_a_card_photo_that_cannot_be_fetched_says_so(stored_card, encoder, monkeypatch) -> None:
    async def down(url):
        return None

    monkeypatch.setattr(profile, "fetch_card_photo", down)

    result = _style("like", _CARD_URL)

    assert result.exit_code == 1
    assert style_cli.NO_CARD_PHOTO in result.stdout


@pytest.mark.parametrize(
    ("on", "extra", "shown"), [(True, True, True), (False, True, False), (True, False, False)]
)
def test_the_console_report_says_how_to_vote_when_style_works(
    capsys, monkeypatch, on, extra, shown
):
    from tests.cli.test_report import _result

    from buysmarter.capabilities.provenance import CapabilityState
    from buysmarter.cli import console

    monkeypatch.setattr(style, "available", lambda: extra)
    state = CapabilityState(
        name="style", label="Стиль", level="category", on=on, active=on, auto=on
    )
    result = _result().model_copy(update={"capabilities": [state]})

    console.render(result, 3)

    assert (style_cli.VOTE_HINT in capsys.readouterr().out) == shown
