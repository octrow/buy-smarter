"""Spike step 2: do the liked brands rise to the top when Kaspi has them in the pool?"""

import json
import re
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageOps

LIKES, SPIKE = Path(sys.argv[1]), Path(sys.argv[2])
GROUPS = {
    "sneakers": "mens-sneakers-2026",
    "boots": "mens-winter-boots-2026",
    "jackets": "mens-winter-jackets-and-parkas",
}
BRANDS = re.compile(
    r"new balance|adidas|vans|converse|asics|puma|danner|north face|blundstone|columbia|patagonia|canada goose",
    re.I,
)
EXTS = {".jpg", ".jpeg", ".png", ".webp"}
TOP = 16


def pixels(path: Path) -> np.ndarray:
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB").resize((224, 224), Image.BICUBIC)
    return ((np.asarray(img, dtype=np.float32) / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)


def embed(sess: ort.InferenceSession, paths: list[Path]) -> np.ndarray:
    parts = [
        sess.run(None, {"pixel_values": np.stack([pixels(p) for p in paths[i : i + 16]])})[0]
        for i in range(0, len(paths), 16)
    ]
    e = np.concatenate(parts)
    return e / np.linalg.norm(e, axis=1, keepdims=True)


def pool() -> list[dict]:
    """Generic and brand-query Kaspi cards, one per product id, with their image path."""
    seen, rows = set(), []
    for sub in ("kaspi", "brands"):
        for r in json.loads((SPIKE / sub / "kaspi.json").read_text()):
            if r["id"] not in seen:
                seen.add(r["id"])
                rows.append({**r, "path": SPIKE / sub / "img" / r["file"], "brand": bool(BRANDS.search(r["title"]))})
    return rows


def sheet(rows: list[dict], scores: np.ndarray, path: Path) -> None:
    order = np.argsort(-scores)[:TOP]
    w, h, per = 200, 230, 8
    canvas = Image.new("RGB", (w * per, h * (TOP // per)), "white")
    draw = ImageDraw.Draw(canvas)
    for n, i in enumerate(order):
        img = Image.open(rows[i]["path"]).convert("RGB")
        img.thumbnail((w - 10, h - 40))
        x, y = (n % per) * w + 5, (n // per) * h + 5
        canvas.paste(img, (x, y))
        mark = "BRAND " if rows[i]["brand"] else ""
        draw.text((x, y + h - 40), f"{mark}{scores[i]:.3f} {rows[i]['price']}", fill="black")
    canvas.save(path)


def main() -> None:
    sess = ort.InferenceSession(str(SPIKE / "models" / "vision_model_int8.onnx"), providers=["CPUExecutionProvider"])
    rows = pool()
    kaspi = embed(sess, [r["path"] for r in rows])
    outfits = embed(sess, sorted(p for p in (LIKES / "mens-outfit-2026").iterdir() if p.suffix.lower() in EXTS))
    for group, folder in GROUPS.items():
        idx = np.flatnonzero([r["group"] == group for r in rows])
        likes = embed(sess, sorted(p for p in (LIKES / folder).iterdir() if p.suffix.lower() in EXTS))
        scores = (kaspi[idx] @ np.concatenate([likes, outfits]).T).max(1)
        brand = np.array([rows[i]["brand"] for i in idx])
        top = np.argsort(-scores)[:TOP]
        print(f"{group:8s} pool {len(idx)}, brand cards {brand.mean():.0%}; in top-{TOP}: {brand[top].mean():.0%}; "
              f"brand mean rank pct {np.mean([(scores[brand] > s).mean() for s in scores[~brand]]):.2f}")
        sheet([rows[i] for i in idx], scores, SPIKE / "sheets" / f"mix-{group}.png")


if __name__ == "__main__":
    main()
