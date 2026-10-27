"""Adding many photos: batch by batch, with progress, and a cancel keeps what is done."""

import asyncio

import pytest

from buysmarter.style import profile


@pytest.fixture
def folder(tmp_path, photo):
    looks = tmp_path / "looks"
    looks.mkdir()
    for i in range(20):
        photo(f"{i}.jpg", (i * 12, 40, 90)).rename(looks / f"{i:02d}.jpg")
    return looks


async def test_progress_is_reported_after_every_batch(folder, encoder) -> None:
    seen: list[tuple[int, int]] = []

    added = await profile.add_photos([folder], disliked=False, progress=lambda *p: seen.append(p))

    assert seen == [(16, 20), (20, 20)]
    assert len(added.photos) == 20


async def test_a_cancel_keeps_the_batches_already_added(folder, encoder) -> None:
    task: asyncio.Task | None = None

    def cancel_after_first(done: int, total: int) -> None:
        assert task is not None
        task.cancel()

    task = asyncio.create_task(profile.add_photos([folder], False, progress=cancel_after_first))
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len((await profile.load()).photos) == 16
