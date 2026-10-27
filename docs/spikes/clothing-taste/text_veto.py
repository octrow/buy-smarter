"""Spike step 3: a "never" rule in words — which Kaspi sneakers does it hit, and what is left on top?"""

import numpy as np
import onnxruntime as ort
import tokenizers

from brand_mix import EXTS, LIKES, SPIKE, embed, pool, sheet

NEVER = [
    "sneakers with a cheap glossy white plastic sole",
    "shiny smooth white plastic platform outsole",
    "cheap plastic shoe with a slippery white sole",
]
PENALTY = 0.15  # taste points taken off per unit of the never-rule z-score above zero


def text_embed(texts: list[str]) -> np.ndarray:
    tok = tokenizers.Tokenizer.from_file(str(SPIKE / "models" / "tokenizer.json"))
    ids = np.array([e.ids for e in tok.encode_batch([t.lower() for t in texts])], dtype=np.int64)
    sess = ort.InferenceSession(str(SPIKE / "models" / "text_model_int8.onnx"), providers=["CPUExecutionProvider"])
    e = sess.run(None, {"input_ids": ids})[0]
    return e / np.linalg.norm(e, axis=1, keepdims=True)


def main() -> None:
    vision = ort.InferenceSession(str(SPIKE / "models" / "vision_model_int8.onnx"), providers=["CPUExecutionProvider"])
    rows = [r for r in pool() if r["group"] == "sneakers"]
    cards = embed(vision, [r["path"] for r in rows])
    likes = embed(vision, sorted(p for p in (LIKES / "mens-sneakers-2026").iterdir() if p.suffix.lower() in EXTS))
    never = (cards @ text_embed(NEVER).T).mean(1)
    z = (never - never.mean()) / never.std()
    taste = (cards @ likes.T).max(1)
    final = taste - PENALTY * np.clip(z, 0, None)
    print(f"sneakers {len(rows)}; never-rule raw sims {never.min():.3f}..{never.max():.3f}")
    sheet(rows, z, SPIKE / "sheets" / "never-hits.png")  # top 16 = strongest "white plastic sole"
    sheet(rows, final, SPIKE / "sheets" / "never-final.png")  # top 16 after the penalty
    moved = np.argsort(-taste)[:16]
    print("of the taste top-16, pushed out by the rule:", len(set(moved) - set(np.argsort(-final)[:16])))


main()
