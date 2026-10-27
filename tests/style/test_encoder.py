"""SigLIP preprocessing and unit-length embeddings, with stand-in ONNX sessions."""

import io

import numpy as np
from PIL import Image

from buysmarter.style.encoder import BATCH, Encoder, pixels

_DIM = 768


def _jpeg(color: tuple[int, int, int], size: tuple[int, int] = (300, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG", quality=100)
    return out.getvalue()


class _Session:
    """Answers every batch with fixed non-unit vectors and records the batch sizes."""

    def __init__(self, input_name: str) -> None:
        self.input_name = input_name
        self.batches: list[int] = []

    def run(self, _outputs, feeds):
        batch = feeds[self.input_name]
        self.batches.append(len(batch))
        return [np.full((len(batch), _DIM), 3.0, dtype=np.float32)]


class _Tokenizer:
    def encode_batch(self, texts):
        return [type("E", (), {"ids": [len(t)] * 64})() for t in texts]


def test_a_photo_becomes_a_224_square_scaled_to_minus_one_one() -> None:
    x = pixels(_jpeg((255, 0, 0)))

    assert x.shape == (3, 224, 224) and x.dtype == np.float32
    assert np.allclose(x[0], 1.0, atol=0.02)  # red: (255/255 - 0.5) / 0.5
    assert np.allclose(x[1:], -1.0, atol=0.02)


def test_photos_are_encoded_in_batches_to_unit_vectors() -> None:
    vision = _Session("pixel_values")
    encoder = Encoder(vision, _Session("input_ids"), _Tokenizer())

    vectors = encoder.images([_jpeg((10, 20, 30))] * (BATCH + 4))

    assert vectors.shape == (BATCH + 4, _DIM)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    assert vision.batches == [BATCH, 4]


def test_phrases_are_lowercased_tokenized_and_encoded_to_unit_vectors() -> None:
    text = _Session("input_ids")
    encoder = Encoder(_Session("pixel_values"), text, _Tokenizer())

    vectors = encoder.texts(["Glossy White Sole", "logo"])

    assert vectors.shape == (2, _DIM)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    assert text.batches == [2]
