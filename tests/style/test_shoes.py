"""Shoes cut out of a sample photo by the clothes segmenter's labels."""

import io

import numpy as np
from PIL import Image

from buysmarter.style import shoes

_LEFT_SHOE = shoes.SHOE_LABELS[0]


def _jpeg(size=(100, 100), colour=(30, 60, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "JPEG")
    return buf.getvalue()


def _labels(box: tuple[int, int, int, int] | None = None, dots: bool = False):
    def labels(img: Image.Image) -> np.ndarray:
        mask = np.zeros((img.height, img.width), dtype=np.uint8)
        if box is not None:
            x0, y0, x1, y1 = box
            mask[y0:y1, x0:x1] = _LEFT_SHOE
        if dots:
            mask[::10, ::10] = _LEFT_SHOE
        return mask

    return labels


def test_the_shoes_are_cut_out_on_white() -> None:
    piece = shoes.shoe_crop(_jpeg(), _labels((60, 70, 90, 90)))

    assert piece is not None
    with Image.open(io.BytesIO(piece)) as img:
        assert 30 <= img.width <= 34 and 20 <= img.height <= 24  # the box and a 5% margin
        assert img.getpixel((0, 0))[0] > 240  # the margin outside the shoe is white


def test_a_photo_without_shoes_gives_none() -> None:
    assert shoes.shoe_crop(_jpeg(), _labels()) is None
    assert shoes.shoe_crop(_jpeg(), _labels((0, 0, 3, 3))) is None  # a speck, not shoes


def test_scattered_bits_are_not_shoes() -> None:
    assert shoes.shoe_crop(_jpeg(), _labels(dots=True)) is None
