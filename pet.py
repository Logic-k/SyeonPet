"""Syeon desktop pet — sprite rendering, window flags, drag physics.

Single window design: the pet sprite sits at the bottom of the window and the
speech bubble is a child widget on top. setMask() punches real OS-level holes
through the empty regions, so clicks land on the pet's body but pass straight
through transparent pixels — the way Pelia/Shimeji do it.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QPixmap, QRegion, QTransform
from PySide6.QtWidgets import QWidget

ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets"


class SpriteBank:
    """Loads sprite-sheet-alpha.png + manifest.json emitted by sprite-gen compose-atlas."""

    def __init__(self, assets: Path = ASSETS):
        self.sheet = QPixmap(str(assets / "sprite-sheet-alpha.png"))
        manifest = json.loads((assets / "manifest.json").read_text(encoding="utf-8"))
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

    def has(self, state: str) -> bool:
        return state in self.states


class PetWidget(QWidget):
    """Frameless, translucent, always-on-top window that renders the current frame."""

    clicked = Signal()
    double_clicked = Signal()

    GRAVITY = 2600.0       # px/s^2
    BOUNCE = 0.32          # restitution on landing
    GROUND_FRICTION = 0.80

    def __init__(self, sprites: SpriteBank, display_size: int = 168):
        super().__init__()
        self.sprites = sprites
        self.display_size = display_size
        self.win_width = max(display_size, 300)  # room for the bubble
        self.base_height = display_size
        self.state = "idle"
        self.frame_idx = 0
        self._facing_left = False
        self.vx = 0.0
        self.vy = 0.0
        self._frame_elapsed = 0.0
        self.time_scale = 1.0  # >1 slows sprite animation playback
        self.crossfade = False  # blend adjacent frames (off: ghosting)
        self.BLEND_MS = 40
        self.motion_amp = 0.8  # procedural squash/bob/tilt amplitude
        self._last_tick = time.monotonic()
        self._drag_offset = QPoint(0, 0)
        self._dragging = False
        self._drag_hist: list[tuple[float, QPoint]] = []
        self._one_shot_done = False
        self.bubble_widget: QWidget | None = None  # set by app

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # no taskbar entry
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(self.win_width, self.base_height)

        self.tick = QTimer(self)
        self.tick.setInterval(16)
        self.tick.setTimerType(Qt.TimerType.PreciseTimer)
        self.tick.timeout.connect(self._on_tick)
        self.tick.start()

        self._pm_cache: dict = {}    # (state, idx, facing) -> scaled QPixmap
        self._mask_cache: dict = {}  # (state, idx, facing) -> grown QRegion

        # spawn: bottom-center of the primary screen's usable area
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.center().x() - self.win_width // 2,
                  geo.bottom() - self.base_height)
        self._update_mask()

    # ---------- window shape / click-through ----------

    @property
    def facing_left(self) -> bool:
        return self._facing_left

    @facing_left.setter
    def facing_left(self, v: bool):
        v = bool(v)
        if v == self._facing_left:
            return
        self._facing_left = v
        self._update_mask()
        self.update()

    def _frame_pm(self, idx: int) -> QPixmap:
        meta = self.sprites.states[self.state]
        pm = meta["frames"][idx % len(meta["frames"])]
        if self.facing_left:
            pm = pm.transformed(QTransform().scale(-1, 1))
        return pm

    def _current_pm(self) -> QPixmap:
        """The frame about to be drawn, with facing applied — mask and paint
        must derive from this same pixmap or the mask clips the sprite."""
        meta = self.sprites.states[self.state]
        return self._frame_pm(min(self.frame_idx, len(meta["frames"]) - 1))

    def _blend_info(self):
        """(next_frame_idx, blend_t 0..1) while fading into the next frame."""
        meta = self.sprites.states[self.state]
        n = len(meta["frames"])
        idx = min(self.frame_idx, n - 1)
        dur = meta["durations"][idx % len(meta["durations"])] * self.time_scale
        nxt = ((idx + 1) % n) if meta["loop"] else (idx + 1 if idx + 1 < n else None)
        if nxt is None or dur <= 0:
            return None, 0.0
        blend = min(self.BLEND_MS, dur * 0.35)
        t = (self._frame_elapsed - (dur - blend)) / blend
        return nxt, max(0.0, min(1.0, t))

    def _scaled_pm(self, idx: int) -> QPixmap:
        key = (self.state, idx, self.facing_left)
        pm = self._pm_cache.get(key)
        if pm is None:
            pm = self._frame_pm(idx).scaled(
                self.display_size, self.display_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._pm_cache[key] = pm
        return pm

    def _cur_scaled_pm(self) -> QPixmap:
        meta = self.sprites.states[self.state]
        return self._scaled_pm(min(self.frame_idx, len(meta["frames"]) - 1))

    def _sprite_draw_rect(self) -> tuple[int, int, int, int]:
        """Where the scaled frame lands inside this window (bottom-center)."""
        scaled = self._cur_scaled_pm()
        x = (self.win_width - scaled.width()) // 2
        y = self.height() - scaled.height()
        return x, y, scaled.width(), scaled.height()

    def _mask_region(self, idx: int) -> QRegion:
        key = (self.state, idx, self.facing_left)
        region = self._mask_cache.get(key)
        if region is None:
            region = QRegion(self._scaled_pm(idx).mask())
            if self.motion_amp > 0:  # slack for procedural transform wobble
                grown = QRegion(region)
                for dx, dy in ((4, 0), (-4, 0), (0, 4), (0, -4)):
                    grown |= region.translated(dx, dy)
                region = grown
            self._mask_cache[key] = region
        return QRegion(region)

    def _update_mask(self):
        """OS-level hit region: the sprite's opaque pixels + bubble rect."""
        meta = self.sprites.states[self.state]
        idx = min(self.frame_idx, len(meta["frames"]) - 1)
        scaled = self._cur_scaled_pm()
        region = self._mask_region(idx)
        x = (self.win_width - scaled.width()) // 2
        y = self.height() - scaled.height()
        region.translate(x, y)
        if self.bubble_widget is not None and self.bubble_widget.isVisible():
            region |= QRegion(self.bubble_widget.geometry())
        self.setMask(region)

    def _relayout(self):
        """Grow the window upward when the bubble opens; feet stay on the ground."""
        floor = self.ground_y()
        extra = self.bubble_widget.height() + 4 if (
            self.bubble_widget is not None and self.bubble_widget.isVisible()) else 0
        new_h = self.base_height + extra
        if new_h != self.height():
            self.resize(self.win_width, new_h)
        self.move(self.x(), floor - self.height())
        if self.bubble_widget is not None and self.bubble_widget.isVisible():
            self.bubble_widget.move((self.win_width - self.bubble_widget.width()) // 2, 2)
        self._update_mask()

    # ---------- animation ----------

    def _on_tick(self):
        now = time.monotonic()
        dt_ms = (now - self._last_tick) * 1000.0
        self._last_tick = now
        self._advance_frame(dt_ms)
        if self.crossfade and self._blend_info()[1] > 0.0:
            self.update()

    def set_state(self, state: str):
        if state == self.state or not self.sprites.has(state):
            return
        self.state = state
        self.frame_idx = 0
        self._frame_elapsed = 0.0
        self._one_shot_done = False
        self._update_mask()
        self.update()

    def _cycle_phase(self) -> float:
        meta = self.sprites.states[self.state]
        total = sum(meta["durations"]) * self.time_scale
        if total <= 0:
            return 0.0
        passed = (sum(meta["durations"][:self.frame_idx]) * self.time_scale
                  + self._frame_elapsed)
        return (passed / total) % 1.0

    def _proc_fx(self):
        """Continuous transform for a lively feel: (sx, sy, rot_deg, dx, dy)."""
        a = self.motion_amp
        if a <= 0:
            return 1.0, 1.0, 0.0, 0.0, 0.0
        st = self.state
        ph = self._cycle_phase()
        if st == "walk":
            bob = abs(math.sin(ph * 2 * math.pi * 2))
            tilt = math.sin(ph * 2 * math.pi * 2) * 1.4 * a
            return 1.0, 1.0, tilt, 0.0, -2.5 * bob * a
        if st == "dance":
            return 1.0, 1.0, math.sin(ph * 2 * math.pi) * 3.0 * a, 0.0, 0.0
        if st in ("idle", "sleep", "liesleep", "still", "drag"):
            b = math.sin(time.monotonic() * 2.0) * a
            return 1.0 + 0.008 * b, 1.0 + 0.012 * b, 0.0, 0.0, 0.0
        return 1.0, 1.0, 0.0, 0.0, 0.0

    def _advance_frame(self, dt_ms: float):
        meta = self.sprites.states[self.state]
        self._frame_elapsed += dt_ms
        durations = meta["durations"]
        changed = False
        while self._frame_elapsed >= \
                durations[self.frame_idx % len(durations)] * self.time_scale:
            self._frame_elapsed -= \
                durations[self.frame_idx % len(durations)] * self.time_scale
            self.frame_idx += 1
            changed = True
            if self.frame_idx >= len(meta["frames"]):
                if meta["loop"]:
                    self.frame_idx = 0
                else:
                    self.frame_idx = len(meta["frames"]) - 1
                    self._one_shot_done = True
                    break
        if changed:
            self._update_mask()
            self.update()
        elif self.motion_amp > 0 and self.state in (
                "walk", "idle", "dance", "sleep", "liesleep", "still", "drag"):
            self.update()  # procedural transform animates every tick

    def one_shot_finished(self) -> bool:
        return self._one_shot_done

    # ---------- geometry helpers ----------

    def ground_y(self) -> int:
        """Y of the usable-desktop floor under the pet's current screen."""
        center = self.geometry().center()
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        return screen.availableGeometry().bottom()

    def screen_bounds(self):
        center = self.geometry().center()
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def sprite_rect_global(self):
        """The pet's sprite rect in global coords (for bubble/UX references)."""
        x, y, w, h = self._sprite_draw_rect()
        return self.geometry().adjusted(x, y, x + w - self.width(), y + h - self.height())

    # ---------- physics ----------

    def physics_step(self, dt: float):
        """Integrate gravity toss; returns True once landed and settled."""
        self.vy += self.GRAVITY * dt
        pos = self.pos() + QPointF(self.vx * dt, self.vy * dt).toPoint()
        floor = self.ground_y() - self.height()
        bounds = self.screen_bounds()
        landed = False
        if pos.y() >= floor:
            pos.setY(floor)
            if abs(self.vy) > 240:
                self.vy = -self.vy * self.BOUNCE
                self.vx *= 0.7
            else:
                self.vy = 0.0
                self.vx *= self.GROUND_FRICTION
                landed = abs(self.vx) < 12
        pos.setX(max(bounds.left() - 20, min(pos.x(), bounds.right() - self.width() + 20)))
        self.move(pos)
        return landed

    # ---------- input ----------

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = e.globalPosition().toPoint() - self.pos()
            self._drag_hist = [(time.monotonic(), e.globalPosition().toPoint())]
            self.vx = self.vy = 0.0

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._dragging:
            gp = e.globalPosition().toPoint()
            self.move(gp - self._drag_offset)
            self._drag_hist.append((time.monotonic(), gp))
            self._drag_hist = self._drag_hist[-6:]

    def mouseReleaseEvent(self, e: QMouseEvent):
        if e.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        self._dragging = False
        if len(self._drag_hist) >= 2:
            (t0, p0), (t1, p1) = self._drag_hist[0], self._drag_hist[-1]
            dt = max(t1 - t0, 1e-3)
            self.vx = (p1.x() - p0.x()) / dt
            self.vy = (p1.y() - p0.y()) / dt
            if abs(self.vx) < 60 and abs(self.vy) < 60 and (p1 - p0).manhattanLength() < 8:
                self.clicked.emit()
        else:
            self.clicked.emit()

    def mouseDoubleClickEvent(self, e: QMouseEvent):
        self.double_clicked.emit()

    # ---------- paint ----------

    def _draw_scaled(self, p: QPainter, scaled: QPixmap):
        p.drawPixmap((self.win_width - scaled.width()) // 2,
                     self.height() - scaled.height(), scaled)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        sx, sy, rot, dx, dy = self._proc_fx()
        if sx != 1.0 or sy != 1.0 or rot or dx or dy:
            rx, ry, rw, rh = self._sprite_draw_rect()
            ax, ay = rx + rw / 2 + dx, ry + rh + dy  # bottom-center anchor
            p.translate(ax, ay)
            p.rotate(rot)
            p.scale(sx, sy)
            p.translate(-rx - rw / 2, -ry - rh)
        meta = self.sprites.states[self.state]
        idx = min(self.frame_idx, len(meta["frames"]) - 1)
        self._draw_scaled(p, self._scaled_pm(idx))
        if self.crossfade:
            nxt, t = self._blend_info()
            if nxt is not None and t > 0.0:
                p.setOpacity(t)
                self._draw_scaled(p, self._scaled_pm(nxt))
        p.end()

    def showEvent(self, e):
        super().showEvent(e)
        self._update_mask()

    # ---------- platform toggles ----------

    def set_click_through(self, enabled: bool):
        """Whole window stops taking input (pet already ignores empty pixels)."""
        if sys.platform == "win32":
            import ctypes
            hwnd = int(self.winId())
            gwl_exstyle = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            style = ctypes.windll.user32.GetWindowLongPtrW(hwnd, gwl_exstyle)
            if enabled:
                style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
            else:
                style &= ~WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongPtrW(hwnd, gwl_exstyle, style)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
            if enabled:
                self.clearFocus()
