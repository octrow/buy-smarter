"""Which screenshots changed between two screenshots.py runs, and by how much.

    uv run --extra gui python <skill dir>/shotdiff.py BEFORE_DIR AFTER_DIR

Prints `changed  12.34%  dark/report.png` per differing image, `new`/`gone` for unmatched
ones; exit 1 when anything differs. Pure QImage, no extra dependency.
"""

import sys
from pathlib import Path

from PySide6.QtGui import QImage

_FORMAT = QImage.Format.Format_RGB32


def main(before: Path, after: Path) -> int:
    names = {p.relative_to(before) for p in before.rglob("*.png")}
    names |= {p.relative_to(after) for p in after.rglob("*.png")}
    lines = [line for name in sorted(names) if (line := difference(before / name, after / name))]
    print(*lines, f"{len(lines)} of {len(names)} differ", sep="\n")
    return 1 if lines else 0


def difference(old: Path, new: Path) -> str | None:
    """One report line for a pair that differs, None when the images match."""
    name = new.name if new.exists() else old.name
    if not old.exists() or not new.exists():
        return f"{'new' if new.exists() else 'gone':<8}           {new.parent.name}/{name}"
    share = changed_share(QImage(str(old)), QImage(str(new)))
    return f"changed  {share:7.2%}  {new.parent.name}/{name}" if share else None


def changed_share(a: QImage, b: QImage) -> float:
    """Share of differing pixels, 1.0 when the sizes differ."""
    if a.size() != b.size():
        return 1.0
    a, b = a.convertToFormat(_FORMAT), b.convertToFormat(_FORMAT)
    # ponytail: byte compare per row, O(w*h) in Python is ~1 s for 16 screens; numpy if it bites
    width = a.width() * 4
    diff = 0
    for y in range(a.height()):
        ra, rb = bytes(a.constScanLine(y))[:width], bytes(b.constScanLine(y))[:width]
        if ra != rb:
            diff += sum(ra[i : i + 4] != rb[i : i + 4] for i in range(0, width, 4))
    return diff / (a.width() * a.height())


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
