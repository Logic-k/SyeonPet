"""Strengthen washed-out sprite outlines without touching the alpha mask.

The idle row art has a thin, pale-gray silhouette edge that disappears against
bright wallpapers. This pass darkens desaturated/light pixels near each frame's
silhouette toward the character's native dark-outline tone, keeping every
pixel's alpha value (and therefore click masks, anchors and wardrobe
decoration geometry) identical.

Guards:
- face/leaf detector colors are left untouched so dynamic anchor detection
  produces identical results
- modified pixels keep max(r,g,b) >= 90 so wardrobe's "dark foreground must
  survive clothing" rule never applies to them
- frames shared with other sheets (bridge endpoints identical to idle) are
  strengthened too when --propagate is given

Usage:  pythonw tools/strengthen_outline.py --states idle [--propagate] [--dry-run]
Writes sheets in place and prints old->new frame digests (QPixmap-path sha) as JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Lib" / "site-packages"))

from PySide6.QtGui import QImage, QPixmap, QPainter
from PySide6.QtWidgets import QApplication

ASSETS = ROOT / "assets"
OPTIONAL_SHEETS = (
    ASSETS / "thinking",
    ASSETS / "extra_motions" / "stretch",
    ASSETS / "extra_motions" / "bow",
    ASSETS / "motion_bridges" / "sleep_bridge",
    ASSETS / "motion_bridges" / "turn_bridge",
)
EDGE_DEPTH = 3.4          # 어두워지는 안쪽 테두리 깊이(px)
EDGE_BLEND = ((1.5, 0.90), (2.5, 0.55), (3.4, 0.28))  # (거리 상한, 혼합 비율)
SOLID_ALPHA = 60          # 실루엣 기준 알파
FRINGE_ALPHA = 215        # 이 알파 이하는 반투명 가장자리로 처리
DESAT_LIMIT = 60          # 무채색 판정 (max-min)
LIGHT_LUM = 150           # 밝은 픽셀 판정
DARK_KEEP_LUM = 110       # 이미 어두운 픽셀은 건드리지 않음
TARGET_MAX = 100          # 보정 후 최대 채널 상한 (옷장 보호 규칙 여유)


def _lum(r: int, g: int, b: int) -> float:
    return (r * 299 + g * 587 + b * 114) / 1000.0


def _is_face_color(r: int, g: int, b: int) -> bool:
    return r > 180 and b > 130 and r - g > 20 and abs(r - b) < 85


def _is_leaf_color(r: int, g: int, b: int) -> bool:
    return g > 100 and r < 140 and b < 100 and g > r * 1.3 and g > b * 1.5


def _native_outline_color(px: bytearray) -> tuple[int, int, int]:
    """Median of the darkest opaque pixels = the art's own outline tone."""
    darks = []
    for i in range(0, len(px), 4):
        b, g, r, a = px[i], px[i + 1], px[i + 2], px[i + 3]  # ARGB32 = BGRA
        if a > 200 and _lum(r, g, b) < 75:
            darks.append((r, g, b))
    if not darks:
        return (95, 82, 105)
    darks.sort(key=lambda c: _lum(*c))
    band = darks[: max(len(darks) // 2, 8)]
    n = len(band)
    r = sum(c[0] for c in band) // n
    g = sum(c[1] for c in band) // n
    b = sum(c[2] for c in band) // n
    peak = max(r, g, b)
    if peak < TARGET_MAX:  # 옷장 'dark foreground' 규칙(max<90)에 걸리지 않도록 상향
        scale = TARGET_MAX / max(peak, 1)
        r, g, b = (min(255, round(c * scale)) for c in (r, g, b))
    return (r, g, b)


def _distance_to_edge(alpha: list[int], w: int, h: int) -> list[float]:
    """Chamfer distance transform: distance from each solid px to non-solid."""
    inf = 1e9
    d = [0.0 if a < SOLID_ALPHA else inf for a in alpha]
    for y in range(h):
        row = y * w
        for x in range(w):
            i = row + x
            if d[i] == 0.0:
                continue
            best = d[i]
            if x:
                best = min(best, d[i - 1] + 1.0)
                if y:
                    best = min(best, d[i - 1 - w] + 1.42)
            if x + 1 < w and y:
                best = min(best, d[i + 1 - w] + 1.42)
            if y:
                best = min(best, d[i - w] + 1.0)
            d[i] = best
    for y in range(h - 1, -1, -1):
        row = y * w
        for x in range(w - 1, -1, -1):
            i = row + x
            if d[i] == 0.0:
                continue
            best = d[i]
            if x + 1 < w:
                best = min(best, d[i + 1] + 1.0)
                if y + 1 < h:
                    best = min(best, d[i + 1 + w] + 1.42)
            if x and y + 1 < h:
                best = min(best, d[i - 1 + w] + 1.42)
            if y + 1 < h:
                best = min(best, d[i + w] + 1.0)
            d[i] = best
    return d


def _strengthen_frame(px: bytearray, w: int, h: int,
                      dark: tuple[int, int, int]) -> int:
    alpha = [px[i + 3] for i in range(0, len(px), 4)]
    dist = _distance_to_edge(alpha, w, h)
    dr, dg, db = dark
    changed = 0
    for i in range(w * h):
        a = alpha[i]
        if a < 10:
            continue
        o = i * 4
        b, g, r = px[o], px[o + 1], px[o + 2]  # ARGB32 = BGRA
        if _is_face_color(r, g, b) or _is_leaf_color(r, g, b):
            continue  # 동적 앵커 감지 입력 보존
        sat = max(r, g, b) - min(r, g, b)
        lum = _lum(r, g, b)
        if sat >= DESAT_LIMIT and lum <= LIGHT_LUM:
            continue
        if lum <= DARK_KEEP_LUM:
            continue
        if a <= FRINGE_ALPHA:
            weight = 1.0  # 반투명 가장자리: 알파 유지, 색만 테두리색으로
        else:
            weight = 0.0
            for limit, wgt in EDGE_BLEND:
                if dist[i] <= limit:
                    weight = wgt
                    break
            if not weight:
                continue
        inv = 1.0 - weight
        nb = round(db * weight + b * inv)
        ng = round(dg * weight + g * inv)
        nr = round(dr * weight + r * inv)
        if _is_face_color(nr, ng, nb) or _is_leaf_color(nr, ng, nb):
            continue  # 옅은 핑크가 보정 후 볼 패턴과 일치하면 감지 입력이 바뀜
        if a > 230:  # 옷장 '채도 있는 전경' 보호(sat>=50)에 걸리지 않게 탈채
            gray = round(_lum(nr, ng, nb))
            while max(nr, ng, nb) - min(nr, ng, nb) >= 48:
                nr = round(nr * 0.7 + gray * 0.3)
                ng = round(ng * 0.7 + gray * 0.3)
                nb = round(nb * 0.7 + gray * 0.3)
        px[o], px[o + 1], px[o + 2] = nb, ng, nr
        changed += 1
    return changed


def _runtime_digest(sheet: QPixmap, rect: dict) -> str:
    """Digest exactly as SpriteBank/_curated_frame produce at runtime."""
    frame = sheet.copy(rect["x"], rect["y"], rect["w"], rect["h"])
    rgba = frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return hashlib.sha256(bytes(rgba.constBits())).hexdigest()


def _process_sheet(image: QImage, sheet_pm: QPixmap, rects: list[dict],
                   only_shas: set[str] | None, dry: bool,
                   label: str, digests: dict) -> int:
    touched = 0
    for rect in rects:
        old = _runtime_digest(sheet_pm, rect)
        if only_shas is not None and old not in only_shas:
            continue
        x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
        region = image.copy(x, y, w, h)
        px = bytearray(region.constBits())
        dark = _native_outline_color(px)
        changed = _strengthen_frame(px, w, h, dark)
        if not dry:
            patched = QImage(bytes(px), w, h, w * 4, QImage.Format.Format_ARGB32)
            p = QPainter(image)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.drawImage(x, y, patched)
            p.end()
        digests[old] = None if dry else "pending"
        touched += 1
        print(f"{label} cell({x},{y}) dark={dark} changed={changed}px")
    return touched


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--states", default="idle")
    parser.add_argument("--propagate", action="store_true",
                        help="also strengthen frames in other sheets sharing a sha")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])

    sheet_path = ASSETS / "sprite-sheet-alpha.png"
    manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
    layout = manifest["frame_layout"]["rows"]
    sheet_pm = QPixmap(str(sheet_path))
    image = QImage(str(sheet_path)).convertToFormat(QImage.Format.Format_ARGB32)
    if image.isNull() or sheet_pm.isNull():
        raise SystemExit(f"cannot load {sheet_path}")

    digests: dict[str, str | None] = {}
    shared_shas: set[str] = set()
    touched: list[tuple[Path, dict]] = []  # (sheet_path, rect) for re-digest
    for state in args.states.split(","):
        rects = layout.get(state)
        if not rects:
            raise SystemExit(f"unknown state: {state}")
        shared_shas.update(_runtime_digest(sheet_pm, r) for r in rects)
        _process_sheet(image, sheet_pm, rects, None, args.dry_run, state, digests)
        touched += [(sheet_path, r) for r in rects]

    saved = [sheet_path]
    if not args.dry_run:
        image.save(str(sheet_path))

    if args.propagate:
        for folder in OPTIONAL_SHEETS:
            mpath = folder / "manifest.json"
            if not mpath.is_file():
                continue
            m = json.loads(mpath.read_text(encoding="utf-8"))
            rows = [r for rows in m["frame_layout"]["rows"].values() for r in rows]
            spath = folder / "sprite-sheet-alpha.png"
            pm = QPixmap(str(spath))
            img = QImage(str(spath)).convertToFormat(QImage.Format.Format_ARGB32)
            before = set(digests)
            n = _process_sheet(img, pm, rows, shared_shas, args.dry_run,
                               folder.name, digests)
            touched += [(spath, r) for r in rows
                        if _runtime_digest(pm, r) in shared_shas]
            if n and not args.dry_run:
                img.save(str(spath))
                saved.append(spath)
            print(f"{folder.name}: {n} shared frames strengthened")

    # final old->new digests on the saved files (runtime QPixmap path)
    if not args.dry_run:
        cache: dict[Path, QPixmap] = {}
        for spath, rect in touched:
            if spath not in cache:
                cache[spath] = QPixmap(str(spath))
            # old digest was captured in digests keys; find matching by order
        # rebuild: digests keys preserve insertion order per touched rect
        keys = list(digests)
        for key, (spath, rect) in zip(keys, touched):
            digests[key] = _runtime_digest(cache[spath], rect)
    print("saved:", ", ".join(p.name for p in saved))
    print(json.dumps(digests, indent=1))


if __name__ == "__main__":
    main()
