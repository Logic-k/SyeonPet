"""Build the petting 'nuzzle' motion from Syeon's original smiling idle frame.

Syeon leans into a stroking hand with her original closed-eye smile. No new
art is drawn: every frame is idle frame 2 (the ^^ smile) with each pixel row
moved sideways by a whole number of pixels, more towards the head and not at
all on the feet line. Because only whole pixels move, the same shift applied
to each curated outfit's idle frame keeps the outfit exactly fitted.

Writes assets/extra_motions/nuzzle/{sprite-sheet-alpha.png,manifest.json},
assets/wardrobe_presets/curated-v2/<choice>-nuzzle.png and the matching
lookup.json entries. Run with the embedded runtime:
    pythonw tools/build_nuzzle_motion.py
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Lib" / "site-packages"))

from PySide6.QtGui import QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ASSETS = ROOT / "assets"
OUT = ASSETS / "extra_motions" / "nuzzle"
CURATED = ASSETS / "wardrobe_presets" / "curated-v2"
CELL = 256
SOURCE_INDEX = 2  # idle frame with the closed-eye ^^ smile
FOOT_Y = 245      # lowest opaque row of the feet; rows from here down stay put
# Lean in degrees per frame (positive = head to the right): sway right, back,
# then left and back towards upright so the cut to idle frame 0 stays small.
LEANS = (1.5, 3.0, 1.0, -1.5, -3.0, -1.0)
DURATION_MS = 170


def frame_sha(pixmap: QPixmap) -> str:
    """Same key as wardrobe_presets._curated_frame."""
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return hashlib.sha256(bytes(image.constBits())).hexdigest()


def shear(frame: QImage, lean: float) -> QImage:
    """Shift each row sideways by whole pixels; nothing is resampled."""
    src = frame.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height, stride = src.width(), src.height(), src.bytesPerLine()
    data = bytes(src.constBits())
    out = bytearray(width * 4 * height)
    slope = math.tan(math.radians(lean))
    for y in range(height):
        shift = round(slope * (FOOT_Y - y)) if y < FOOT_Y else 0
        row = data[y * stride:y * stride + width * 4]
        dropped = row[(width - shift) * 4:] if shift > 0 else row[:-shift * 4] if shift < 0 else b""
        if any(dropped[3::4]):
            raise SystemExit(f"lean {lean}: row {y} would lose opaque pixels at the edge")
        if shift > 0:
            row = bytes(shift * 4) + row[:(width - shift) * 4]
        elif shift < 0:
            row = row[-shift * 4:] + bytes(-shift * 4)
        out[y * width * 4:(y + 1) * width * 4] = row
    return QImage(bytes(out), width, height, width * 4, QImage.Format.Format_RGBA8888).copy()


def strip(frames: list[QImage]) -> QImage:
    """Place frames side by side with an exact byte copy (no painter blending)."""
    width = CELL * len(frames)
    out = bytearray(width * 4 * CELL)
    for index, frame in enumerate(frames):
        src = frame.convertToFormat(QImage.Format.Format_RGBA8888)
        data, stride = bytes(src.constBits()), src.bytesPerLine()
        for y in range(CELL):
            start = (y * width + index * CELL) * 4
            out[start:start + CELL * 4] = data[y * stride:y * stride + CELL * 4]
    return QImage(bytes(out), width, CELL, width * 4, QImage.Format.Format_RGBA8888).copy()


def main() -> None:
    app = QApplication.instance() or QApplication([])  # noqa: F841 - QPixmap needs it
    manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
    rect = manifest["frame_layout"]["rows"]["idle"][SOURCE_INDEX]
    atlas = QPixmap(str(ASSETS / "sprite-sheet-alpha.png"))
    source_pm = atlas.copy(rect["x"], rect["y"], rect["w"], rect["h"])
    lookup_path = CURATED / "lookup.json"
    lookup = json.loads(lookup_path.read_text(encoding="utf-8"))
    entry = lookup["frames"].get(frame_sha(source_pm))
    if not entry or entry[0] != "main":
        raise SystemExit("idle source frame is not in the curated lookup")
    _, cx, cy, _, _ = entry

    base = [shear(source_pm.toImage(), lean) for lean in LEANS]
    OUT.mkdir(parents=True, exist_ok=True)
    sheet_path = OUT / "sprite-sheet-alpha.png"
    if not strip(base).save(str(sheet_path)):
        raise SystemExit("could not write the nuzzle sheet")
    rects = [{"x": i * CELL, "y": 0, "w": CELL, "h": CELL} for i in range(len(LEANS))]
    (OUT / "manifest.json").write_text(json.dumps({
        "characterId": "syeon",
        "source": {"sheet": "assets/sprite-sheet-alpha.png", "row": "idle", "frame": SOURCE_INDEX},
        "method": "whole-pixel row shear about the feet line (no resampling)",
        "lean_degrees": list(LEANS),
        "animation": {"cellWidth": CELL, "cellHeight": CELL, "columns": len(LEANS), "rows": {"nuzzle": {
            "row": 0, "frames": len(LEANS), "durations_ms": [DURATION_MS] * len(LEANS), "loop": False}}},
        "frame_layout": {"sheetWidth": CELL * len(LEANS), "sheetHeight": CELL, "cellWidth": CELL,
                         "cellHeight": CELL, "rows": {"nuzzle": rects}},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Keys must be computed exactly as the app loads the saved sheet.
    reloaded = QPixmap(str(sheet_path))
    keys = [frame_sha(reloaded.copy(r["x"], r["y"], r["w"], r["h"])) for r in rects]
    frames = lookup["frames"]
    for key in keys:
        old = frames.get(key)
        if old is not None and old[0] != "nuzzle":
            raise SystemExit(f"nuzzle frame collides with an existing {old[0]} frame")
    if len(set(keys)) != len(keys):
        raise SystemExit("nuzzle frames are not distinct")
    frames = {k: v for k, v in frames.items() if v[0] != "nuzzle"}
    for index, key in enumerate(keys):
        frames[key] = ["nuzzle", index * CELL, 0, CELL, CELL]

    choices = sorted(p.name[:-len("-main.png")] for p in CURATED.glob("*-main.png"))
    for choice in choices:
        dressed = QPixmap(str(CURATED / f"{choice}-main.png")).copy(cx, cy, CELL, CELL).toImage()
        out = CURATED / f"{choice}-nuzzle.png"
        if not strip([shear(dressed, lean) for lean in LEANS]).save(str(out)):
            raise SystemExit(f"could not write {out.name}")
        print(f"wrote {out.name}")
    lookup["frames"] = frames
    lookup_path.write_text(json.dumps(lookup, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {sheet_path.relative_to(ROOT)} and {len(keys)} lookup entries for {len(choices)} outfits")


if __name__ == "__main__":
    main()
