"""The shoes cut out of a sample photo, on white, as a sample of their own (``style`` extra).

On an outfit photo the shoes are a small corner and the photo's vector is the jacket's: a
sneaker card then ranks by the jacket. Cut out, they rank sneakers by the user's sneakers
(the spike on 125 liked photos: white leather low-tops and Samba-likes rose over chunky
runners). Only shoes: cut-out tops and trousers pulled jackets to black no-name puffers.

The segmenter is SegFormer-B2 trained on clothes (``mattmdjaga/segformer_b2_clothes``):
512x512 in, ImageNet normalisation, per-pixel labels out at a quarter of the size.
"""

import functools
import io
from collections.abc import Callable
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from buysmarter.style.weights import FILES, weights_dir

SHOE_LABELS = (9, 10)  # Left-shoe, Right-shoe
MIN_AREA = 0.005  # of the photo: less is a speck, not a pair of shoes
MIN_FILL = 0.25  # of the cut-out's box: less is bits scattered over the photo
_PAD = 0.05  # margin around the shoes, of the box's longer side
_SIZE = 512
_LABELS_PX = 384  # the longer side the labels are read at: smooth edges, still fast
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

Labels = Callable[[Image.Image], np.ndarray]  # per-pixel label ids, the photo's size


def shoe_crop(data: bytes, labels: Labels) -> bytes | None:
    """The shoes on white as a JPEG; ``None`` when the photo shows none."""
    with Image.open(io.BytesIO(data)) as img:
        rgb = ImageOps.exif_transpose(img).convert("RGB")
    mask = np.isin(labels(rgb), SHOE_LABELS)
    if mask.mean() < MIN_AREA:
        return None
    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    if mask[y0:y1, x0:x1].mean() < MIN_FILL:
        return None
    pad = int(_PAD * max(y1 - y0, x1 - x0))
    y0, x0 = max(0, y0 - pad), max(0, x0 - pad)
    y1, x1 = min(mask.shape[0], y1 + pad), min(mask.shape[1], x1 + pad)
    piece = np.where(mask[..., None], np.asarray(rgb), 255).astype(np.uint8)[y0:y1, x0:x1]
    out = io.BytesIO()
    Image.fromarray(piece).save(out, "JPEG", quality=90)
    return out.getvalue()


class Segmenter:
    """The clothes segmenter; ``labels`` is what ``shoe_crop`` needs."""

    def __init__(self, session: Any) -> None:
        self._session = session

    def labels(self, img: Image.Image) -> np.ndarray:
        x = np.asarray(img.resize((_SIZE, _SIZE), Image.Resampling.BILINEAR), np.float32) / 255
        x = ((x - _MEAN) / _STD).transpose(2, 0, 1)[None].astype(np.float32)
        logits = self._session.run(None, {"pixel_values": x})[0][0]
        scale = _LABELS_PX / max(img.size)
        size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        # Resize the scores, not the labels: a label map blown up 4x has square edges.
        scores = [Image.fromarray(c).resize(size, Image.Resampling.BILINEAR) for c in logits]
        small = Image.fromarray(
            np.stack([np.asarray(s) for s in scores]).argmax(0).astype(np.uint8)
        )
        return np.asarray(small.resize(img.size, Image.Resampling.NEAREST))


@functools.cache
def shared() -> Segmenter:
    """The segmenter from the user cache, loaded once per process."""
    import onnxruntime as ort

    path = str(weights_dir() / FILES["segmenter"].target)
    return Segmenter(ort.InferenceSession(path, providers=["CPUExecutionProvider"]))
