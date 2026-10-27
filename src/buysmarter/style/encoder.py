"""Photos and phrases to unit-length 768-d vectors with the ONNX model (``style`` extra).

Preprocessing is the model's own (``preprocessor_config.json``): RGB, a straight 224x224
bicubic resize, ``(x / 255 - 0.5) / 0.5``, channels first. The text tower is English and
blind to negation: a phrase names the thing itself ("glossy white plastic sole"), never
"no X".
"""

import functools
import io
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from buysmarter.style.weights import FILES, weights_dir

BATCH = 16  # the spike's fastest batch on a laptop CPU
_SIZE = 224


def pixels(data: bytes) -> np.ndarray:
    """One photo as the vision tower's input: float32, (3, 224, 224), in [-1, 1]."""
    with Image.open(io.BytesIO(data)) as img:
        rgb = ImageOps.exif_transpose(img).convert("RGB")
        rgb = rgb.resize((_SIZE, _SIZE), Image.Resampling.BICUBIC)
    return ((np.asarray(rgb, dtype=np.float32) / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)


def _unit(vectors: np.ndarray) -> np.ndarray:
    unit: np.ndarray = (vectors / np.linalg.norm(vectors, axis=1, keepdims=True)).astype(np.float32)
    return unit


class Encoder:
    """The two towers and the tokenizer; ``load`` builds the real ones from the model files."""

    def __init__(self, vision: Any, text: Any, tokenizer: Any) -> None:
        self._vision, self._text, self._tokenizer = vision, text, tokenizer

    @classmethod
    def load(cls, directory: Path) -> "Encoder":
        import onnxruntime as ort
        import tokenizers

        def session(name: str) -> Any:
            path = str(directory / FILES[name].path)
            return ort.InferenceSession(path, providers=["CPUExecutionProvider"])

        tokenizer = tokenizers.Tokenizer.from_file(str(directory / FILES["tokenizer"].path))
        return cls(session("vision"), session("text"), tokenizer)

    def images(self, blobs: list[bytes]) -> np.ndarray:
        """Unit vectors, one per photo, in order."""
        batches = (blobs[i : i + BATCH] for i in range(0, len(blobs), BATCH))
        parts = [self._run_vision(np.stack([pixels(b) for b in batch])) for batch in batches]
        return _unit(np.concatenate(parts))

    def _run_vision(self, batch: np.ndarray) -> np.ndarray:
        out: np.ndarray = self._vision.run(None, {"pixel_values": batch})[0]
        return out

    def texts(self, phrases: list[str]) -> np.ndarray:
        """Unit vectors, one per phrase (lowercased, as the model was trained)."""
        ids = [e.ids for e in self._tokenizer.encode_batch([p.lower() for p in phrases])]
        return _unit(self._text.run(None, {"input_ids": np.array(ids, dtype=np.int64)})[0])


@functools.cache
def shared() -> Encoder:
    """The model from the user cache, loaded once per process (about a second)."""
    return Encoder.load(weights_dir())
