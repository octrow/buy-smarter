"""Spike: does Marqo-FashionSigLIP (ONNX, CPU) tell the user's liked clothes from Kaspi's?

Usage: taste_spike.py <likes dir> <spike dir>. Writes sheets/*.png and prints the numbers.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageOps

LIKES, SPIKE = Path(sys.argv[1]), Path(sys.argv[2])
GROUPS = {  # liked subfolder -> Kaspi group
    "mens-sneakers-2026": "sneakers",
    "mens-winter-boots-2026": "boots",
    "mens-winter-jackets-and-parkas": "jackets",
}
OUTFITS = "mens-outfit-2026"
EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def pixels(path: Path) -> np.ndarray:
    """SigLIP preprocessing: RGB, 224x224 bicubic, (x/255 - 0.5) / 0.5, CHW."""
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB").resize((224, 224), Image.BICUBIC)
    return ((np.asarray(img, dtype=np.float32) / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)


def embed(session: ort.InferenceSession, paths: list[Path], batch: int = 16) -> np.ndarray:
    out = []
    for i in range(0, len(paths), batch):
        x = np.stack([pixels(p) for p in paths[i : i + batch]])
        out.append(session.run(None, {"pixel_values": x})[0])
    e = np.concatenate(out)
    return e / np.linalg.norm(e, axis=1, keepdims=True)


def session(name: str) -> ort.InferenceSession:
    return ort.InferenceSession(str(SPIKE / "models" / name), providers=["CPUExecutionProvider"])


def timing(sess: ort.InferenceSession, paths: list[Path]) -> tuple[float, float]:
    """ms per image at batch 1 and batch 16 (preprocessing included)."""
    embed(sess, paths[:2], 1)  # warm-up
    t = time.perf_counter()
    embed(sess, paths[:16], 1)
    one = (time.perf_counter() - t) / 16 * 1000
    t = time.perf_counter()
    embed(sess, paths[:48], 16)
    many = (time.perf_counter() - t) / 48 * 1000
    return one, many


def auc_leave_one_out(likes: np.ndarray, kaspi: np.ndarray, extra: np.ndarray) -> tuple[float, float]:
    """Mean share of Kaspi cards a held-out like beats: nearest-anchor and centroid scores."""
    near, cent = [], []
    for i in range(len(likes)):
        anchors = np.concatenate([np.delete(likes, i, axis=0), extra])
        held, pool = likes[i] @ anchors.T, kaspi @ anchors.T
        near.append(float((held.max() > pool.max(axis=1)).mean()))
        c = anchors.mean(axis=0)
        cent.append(float((likes[i] @ c > kaspi @ c).mean()))
    return float(np.mean(near)), float(np.mean(cent))


def sheet(rows: list[dict], scores: np.ndarray, title: str, path: Path, n: int = 8) -> None:
    """Top-n and bottom-n Kaspi cards by taste score, one row each."""
    order = np.argsort(-scores)
    picks = [("top", order[:n]), ("bottom", order[-n:])]
    w, h = 200, 230
    canvas = Image.new("RGB", (w * n, h * 2 + 30), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 8), title, fill="black")
    for r, (_label, idx) in enumerate(picks):
        for c, i in enumerate(idx):
            img = Image.open(SPIKE / "kaspi" / "img" / rows[i]["file"]).convert("RGB")
            img.thumbnail((w - 10, h - 40))
            x, y = c * w + 5, 30 + r * h
            canvas.paste(img, (x, y))
            draw.text((x, y + h - 38), f"{scores[i]:.3f}  {rows[i]['price']} ₸", fill="black")
            draw.text((x, y + h - 24), rows[i]["title"][:30], fill="gray")
    canvas.save(path)


def main() -> None:
    rows = json.loads((SPIKE / "kaspi" / "kaspi.json").read_text())
    kaspi_paths = [SPIKE / "kaspi" / "img" / r["file"] for r in rows]
    like_paths = {
        d.name: sorted(p for p in d.iterdir() if p.suffix.lower() in EXTS)
        for d in LIKES.iterdir()
        if d.is_dir()
    }
    int8, fp32 = session("vision_model_int8.onnx"), session("vision_model.onnx")
    print("ms/image int8 (batch1, batch16): %.0f, %.0f" % timing(int8, kaspi_paths))
    print("ms/image fp32 (batch1, batch16): %.0f, %.0f" % timing(fp32, kaspi_paths))

    k8, k32 = embed(int8, kaspi_paths), embed(fp32, kaspi_paths)
    print("int8 vs fp32 cosine: mean %.4f, min %.4f" % ((k8 * k32).sum(1).mean(), (k8 * k32).sum(1).min()))
    likes = {name: embed(int8, paths) for name, paths in like_paths.items()}
    likes32 = {name: embed(fp32, paths) for name, paths in like_paths.items()}
    group = np.array([r["group"] for r in rows])

    print("\nheld-out like beats this share of Kaspi cards (0.5 = chance) — nearest / centroid:")
    for folder, g in GROUPS.items():
        pool = group == g
        for label, emb, kas in (("int8", likes, k8), ("fp32", likes32, k32)):
            near, cent = auc_leave_one_out(emb[folder], kas[pool], np.empty((0, 768)))
            print(f"  {g:8s} {label}: {near:.2f} / {cent:.2f}  (likes {len(emb[folder])}, kaspi {pool.sum()})")
        other = np.concatenate([v for k, v in likes.items() if k not in (folder, OUTFITS)])
        control = np.mean([((x @ other.T).max() > (k8[pool] @ other.T).max(1)).mean() for x in likes[folder]])
        print(f"  {g:8s} control, other-category likes as anchors (0.5 = no photo-source bias): {control:.2f}")

    sheets = SPIKE / "sheets"
    sheets.mkdir(exist_ok=True)
    print("\nTop-10 overlap int8 vs fp32, and sheets:")
    for folder, g in GROUPS.items():
        pool = np.flatnonzero(group == g)
        anchors = np.concatenate([likes[folder], likes[OUTFITS]])
        s8 = (k8[pool] @ anchors.T).max(1)
        s32 = (k32[pool] @ np.concatenate([likes32[folder], likes32[OUTFITS]]).T).max(1)
        overlap = len(set(np.argsort(-s8)[:10]) & set(np.argsort(-s32)[:10]))
        print(f"  {g}: {overlap}/10")
        sheet([rows[i] for i in pool], s8, f"{g}: top 8 (row 1) and bottom 8 (row 2) by taste", sheets / f"{g}.png")
    np.savez(SPIKE / "emb.npz", kaspi=k8, **{k.replace("-", "_"): v for k, v in likes.items()})


main()
