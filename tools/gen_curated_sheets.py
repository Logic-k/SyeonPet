"""Bake curated-v2 outfit sheets for new wardrobe presets.

For each new choice and each kind (main/thinking/stretch/bow/sleep_bridge/
turn_bridge), renders the dynamic-decorated version of every original frame
listed in curated-v2/lookup.json and packs it at the lookup's (x, y) slot.
Run with the embedded runtime: pythonw tools/gen_curated_sheets.py <choice...>
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Lib" / "site-packages"))

from PySide6.QtGui import QImage, QPixmap, QPainter, Qt
from PySide6.QtWidgets import QApplication

ASSETS = ROOT / "assets"
CURATED = ASSETS / "wardrobe_presets" / "curated-v2"

KIND_SOURCES = {
    "main": (ASSETS / "sprite-sheet-alpha.png", ASSETS / "manifest.json"),
    "thinking": (ASSETS / "thinking" / "sprite-sheet-alpha.png", ASSETS / "thinking" / "manifest.json"),
    "stretch": (ASSETS / "extra_motions" / "stretch" / "sprite-sheet-alpha.png", ASSETS / "extra_motions" / "stretch" / "manifest.json"),
    "bow": (ASSETS / "extra_motions" / "bow" / "sprite-sheet-alpha.png", ASSETS / "extra_motions" / "bow" / "manifest.json"),
    "sleep_bridge": (ASSETS / "motion_bridges" / "sleep_bridge" / "sprite-sheet-alpha.png", ASSETS / "motion_bridges" / "sleep_bridge" / "manifest.json"),
    "turn_bridge": (ASSETS / "motion_bridges" / "turn_bridge" / "sprite-sheet-alpha.png", ASSETS / "motion_bridges" / "turn_bridge" / "manifest.json"),
}


def frame_sha(pixmap: QPixmap) -> str:
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return hashlib.sha256(bytes(image.constBits())).hexdigest()


def build_sheet(choice: str, kind: str, entries: dict[str, list],
                decorate) -> None:
    atlas_path, manifest_path = KIND_SOURCES[kind]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    atlas = QPixmap(str(atlas_path))
    if atlas.isNull():
        raise SystemExit(f"atlas missing: {atlas_path}")
    max_x = max(v[1] for v in entries.values()) + 256
    max_y = max(v[2] for v in entries.values()) + 256
    sheet = QImage(max_x, max_y, QImage.Format.Format_ARGB32)
    sheet.fill(Qt.GlobalColor.transparent)
    painter = QPainter(sheet)
    placed: set[str] = set()
    for state, rects in manifest["frame_layout"]["rows"].items():
        for rect in rects:
            frame = atlas.copy(rect["x"], rect["y"], rect["w"], rect["h"])
            sha = frame_sha(frame)
            entry = entries.get(sha)
            if entry is None or entry[0] != kind or sha in placed:
                continue
            dressed = decorate(frame, choice)
            painter.drawPixmap(entry[1], entry[2], dressed)
            placed.add(sha)
    painter.end()
    if placed != set(entries):
        missing = len(set(entries) - placed)
        raise SystemExit(f"{choice}/{kind}: placed {len(placed)} of {len(entries)} frames ({missing} missing)")
    out = CURATED / f"{choice}-{kind}.png"
    sheet.save(str(out))
    print(f"wrote {out.name} ({len(placed)} frames)")


def main() -> None:
    app = QApplication.instance() or QApplication([])
    from wardrobe_presets import decorate_preset

    choices = sys.argv[1:]
    if not choices:
        raise SystemExit("usage: gen_curated_sheets.py <choice> [choice...]")
    lookup = json.loads((CURATED / "lookup.json").read_text(encoding="utf-8"))["frames"]
    by_kind: dict[str, dict[str, list]] = {}
    for sha, entry in lookup.items():
        by_kind.setdefault(entry[0], {})[sha] = entry
    for choice in choices:
        for kind, entries in by_kind.items():
            build_sheet(choice, kind, entries, decorate_preset)


if __name__ == "__main__":
    main()
