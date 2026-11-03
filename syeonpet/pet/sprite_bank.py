"""SpriteBank atlas loader and frame animation repository.

Loads sprite-sheet-alpha.png + manifest.json emitted by sprite-gen compose-atlas,
and manages packaged playback sequences and optional motion bridge rows.
Also houses math/physics helpers for PetWidget.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import time

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QPainter, QPixmap, QRegion

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
ASSETS = ROOT / "assets"


class SpriteBank:
    """Loads sprite-sheet-alpha.png + manifest.json emitted by sprite-gen compose-atlas."""

    def __init__(self, assets: Path = ASSETS):
        self.assets_dir = Path(assets)
        self.sheet = QPixmap(str(self.assets_dir / "sprite-sheet-alpha.png"))
        manifest = json.loads((self.assets_dir / "manifest.json").read_text(encoding="utf-8"))
        layout = manifest["frame_layout"]["rows"]
        anim = manifest["animation"]["rows"]
        self.states: dict[str, dict] = {}
        for name, rects in layout.items():
            meta = anim.get(name, {})
            frames = [self.sheet.copy(r["x"], r["y"], r["w"], r["h"]) for r in rects]
            durations = meta.get("durations_ms") or [1000 // meta.get("fps", 8)] * len(frames)
            self.states[name] = {
                "frames": frames,
                "durations": durations,
                "loop": bool(meta.get("loop", True)),
            }
        self._load_optional_thinking(self.assets_dir)
        for state in ("stretch", "bow"):
            self._load_optional_row(self.assets_dir / "extra_motions" / state, state, loop=False)
        self._load_motion_bridges(self.assets_dir)
        if self.assets_dir.resolve() == ASSETS.resolve():
            self._apply_packaged_playback()

    def _load_motion_bridges(self, assets: Path) -> None:
        for state, reverse in (("sleep_bridge", "wake_bridge"),
                               ("turn_bridge", "turn_front")):
            self._load_optional_row(assets / "motion_bridges" / state, state, loop=False)
            if state in self.states:
                source = self.states[state]
                self.states[reverse] = {
                    "frames": list(reversed(source["frames"])),
                    "durations": list(reversed(source["durations"])),
                    "loop": False,
                }

    def _apply_packaged_playback(self) -> None:
        if "thinking" in self.states:
            self.states["thinking"]["render_offset_y"] = 11
        for state, scale in (("liesleep", 0.82), ("still", 0.90)):
            if state in self.states:
                self.states[state]["render_scale"] = scale
                self.states[state]["render_anchor"] = (128, 245)
        for state, sequence in (("sleep", (0, 1, 2, 3, 2, 1)),
                                ("liesleep", (0, 2, 4, 5, 3, 1)),
                                ("stretch", (0, 1, 2, 3, 2, 4, 5)),
                                ("bow", (0, 1, 2, 3, 2, 4, 5))):
            meta = self.states.get(state)
            if meta is None or len(meta["frames"]) != (4 if state == "sleep" else 6):
                continue
            durations = [meta["durations"][i % len(meta["durations"])] for i in sequence]
            if state == "liesleep":
                durations = [300, 300, 450, 300, 300, 450]
            elif state != "sleep":
                durations[4] = 140
            meta["playback"] = {
                "frames": [meta["frames"][i] for i in sequence],
                "durations": durations,
                "loop": meta["loop"],
                "source_indices": sequence,
                **{k: meta[k] for k in ("render_scale", "render_anchor") if k in meta},
            }

    def _load_optional_thinking(self, assets: Path) -> None:
        self._load_optional_row(assets / "thinking", "thinking", loop=True)

    def _load_optional_row(self, folder: Path, state: str, *, loop: bool) -> None:
        try:
            manifest_file = folder / "manifest.json"
            if not manifest_file.is_file():
                return
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            sheet = QPixmap(str(folder / "sprite-sheet-alpha.png"))
            if sheet.isNull():
                return
            layout = manifest["frame_layout"]["rows"][state]
            meta = manifest["animation"]["rows"][state]
            durations = meta["durations_ms"]
            if (not isinstance(layout, list) or len(layout) != 6
                    or meta.get("frames") != 6 or meta.get("loop") is not loop
                    or not isinstance(durations, list) or len(durations) != 6
                    or any(type(d) is not int or d <= 0 or d > 10000 for d in durations)):
                return
            frames = []
            size = None
            for rect in layout:
                x, y, w, h = (rect[k] for k in ("x", "y", "w", "h"))
                if (any(type(v) is not int for v in (x, y, w, h))
                        or min(x, y) < 0 or min(w, h) <= 0
                        or x + w > sheet.width() or y + h > sheet.height()):
                    return
                if size is None:
                    size = (w, h)
                elif size != (w, h):
                    return
                frame = sheet.copy(x, y, w, h)
                if frame.isNull() or QRegion(frame.mask()).isEmpty():
                    return
                frames.append(frame)
            self.states[state] = {"frames": frames, "durations": list(durations), "loop": loop}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            return

    def has(self, state: str) -> bool:
        return state in self.states


def compute_mask_region(scaled_pm: QPixmap, state: str, facing_left: bool,
                        idx: int, motion_amp: float, reduced_motion: bool,
                        display_size: int, transition_kind: str | None,
                        cache: dict) -> QRegion:
    amp = min(max(motion_amp, 0.0), 2.0) if not reduced_motion and math.isfinite(motion_amp) else 0.0
    padding = 0
    if state == "walk":
        padding = math.ceil(1.2 * display_size * math.sin(math.radians(1.4 * amp)) + 2.5 * amp + 1) if amp else 0
    elif state == "dance":
        padding = math.ceil(1.2 * display_size * math.sin(math.radians(3 * amp)) + 1) if amp else 0
    elif state in ("idle", "sleep", "liesleep", "drag"):
        padding = math.ceil(0.012 * display_size * amp + 1) if amp else 0
    if transition_kind is not None and amp:
        padding += math.ceil(0.06 * display_size * amp + 2)
    key = (state, idx, facing_left, padding)
    region = cache.get(key)
    if region is None:
        region = QRegion(scaled_pm.mask())
        if padding:
            grown, reach = QRegion(region), 0
            while reach < padding:
                step = min(2 * reach + 1, padding - reach)
                b1 = QRegion(grown)
                grown |= b1.translated(step, 0) | b1.translated(-step, 0)
                b2 = QRegion(grown)
                grown |= b2.translated(0, step) | b2.translated(0, -step)
                reach += step
            region = grown
        cache[key] = region
    return QRegion(region)


def compute_proc_fx(state: str, motion_amp: float, reduced_motion: bool,
                    facing_left: bool, phase: float,
                    transition_kind: str | None,
                    transition_elapsed: float, transition_duration: float) -> tuple[float, float, float, float, float]:
    a = motion_amp
    if reduced_motion or not math.isfinite(a) or a <= 0 or state == "still":
        return 1.0, 1.0, 0.0, 0.0, 0.0
    a = min(a, 2.0)
    if state == "walk":
        bob = abs(math.sin(phase * 2 * math.pi * 2))
        res = (1.0, 1.0, math.sin(phase * 2 * math.pi * 2) * 1.4 * a, 0.0, -2.5 * bob * a)
    elif state == "dance":
        res = (1.0, 1.0, math.sin(phase * 2 * math.pi) * 3.0 * a, 0.0, 0.0)
    elif state in ("idle", "sleep", "liesleep", "drag"):
        b = math.sin(time.monotonic() * 2.0) * a
        res = (1.0 + 0.008 * b, 1.0 + 0.012 * b, 0.0, 0.0, 0.0)
    else:
        res = (1.0, 1.0, 0.0, 0.0, 0.0)
    if transition_kind is None:
        return res
    env = math.sin(math.pi * min(1.0, transition_elapsed / transition_duration)) ** 2 * a
    sx, sy, rot, dx, dy = res
    fac = -1 if facing_left else 1
    if transition_kind == "landing":
        sx, sy = sx + 0.035 * env, sy - 0.045 * env
    elif transition_kind == "walk_start":
        sx, sy, rot = sx - 0.012 * env, sy + 0.018 * env, rot + 1.2 * fac * env
    else:
        sx, sy, rot = sx + 0.012 * env, sy - 0.018 * env, rot - 0.8 * fac * env
    return sx, sy, rot, dx, dy


def blend_pixmaps(current: QPixmap, incoming: QPixmap, t: float) -> QPixmap:
    def weighted(src: QPixmap, w: float) -> QPixmap:
        img = src.copy()
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        p.fillRect(img.rect(), QColor(0, 0, 0, round(255 * w)))
        p.end()
        return img
    c1, c2 = weighted(current, 1.0 - t), weighted(incoming, t)
    p = QPainter(c1)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    p.drawPixmap((current.width() - incoming.width()) // 2, current.height() - incoming.height(), c2)
    p.end()
    return c1


def integrate_physics_step(pet, dt: float) -> bool:
    if not math.isfinite(dt) or dt <= 0:
        return False
    dt = min(dt, pet.MAX_PHYSICS_STEP)
    floor = pet.ground_y() - pet.height()
    if pet._physics_position is None or pet.pos() != pet._physics_anchor:
        pet._physics_position = QPointF(pet.pos())
        if pet.y() < floor or pet.vy != 0:
            pet._physics_grounded = False
    pos = QPointF(pet._physics_position)
    bounds = pet.screen_bounds()
    landed = False
    pos.setY(min(pos.y(), floor))
    flight_y = pos.y() + pet.vy * dt + 0.5 * pet.GRAVITY * dt * dt
    if flight_y < floor:
        pet._physics_grounded = False
        pos += QPointF(pet.vx * dt, pet.vy * dt + 0.5 * pet.GRAVITY * dt * dt)
        pet.vy += pet.GRAVITY * dt
    else:
        impact = (-pet.vy + math.sqrt(pet.vy * pet.vy + 2 * pet.GRAVITY * (floor - pos.y()))) / pet.GRAVITY
        impact = max(0.0, min(dt, impact))
        pos.setX(pos.x() + pet.vx * impact)
        pos.setY(floor)
        pet.vy += pet.GRAVITY * impact
        remaining = dt - impact
        if pet.vy > 240:
            pet._physics_grounded = False
            pet.vy = -pet.vy * pet.BOUNCE
            pet.vx *= 0.7
            pos += QPointF(pet.vx * remaining, pet.vy * remaining + 0.5 * pet.GRAVITY * remaining * remaining)
            pet.vy += pet.GRAVITY * remaining
        else:
            if not pet._physics_grounded:
                pet.play_transition("landing")
            pet._physics_grounded = True
            pet.vy = 0.0
            friction = -60.0 * math.log(pet.GROUND_FRICTION)
            decay = math.exp(-friction * remaining)
            pos.setX(pos.x() + pet.vx * (1.0 - decay) / friction)
            pet.vx *= decay
            landed = abs(pet.vx) < 12
    pos.setX(max(bounds.left() - 20, min(pos.x(), bounds.right() - pet.width() + 20)))
    pet._physics_position = pos
    pet._physics_anchor = pos.toPoint()
    pet.move(pet._physics_anchor)
    return landed

