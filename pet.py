"""Syeon desktop pet — sprite rendering, window flags, drag physics, companion petting.

Single window design: the pet sprite sits at the bottom of the window and the
speech bubble is a child widget on top. setMask() punches real OS-level holes
through transparent pixels, so clicks land on the pet's body or bubble.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QPixmap, QRegion, QTransform
from PySide6.QtWidgets import QWidget

from syeonpet.pet.companion_care import HEAD_ZONE_RATIO, HeartParticle, PettingTracker, paint_hearts
from syeonpet.pet.quick_bar import TimerBadge, paint_timer_badge
from syeonpet.pet.sprite_bank import (ASSETS, SpriteBank, blend_pixmaps,
                                      compute_mask_region, compute_proc_fx,
                                      integrate_physics_step)
from wardrobe import (decorate_frame, pack_root, read_pack_choice, read_wardrobe_choice,
                      write_pack_choice, write_wardrobe_choice)

__all__ = ["ASSETS", "SpriteBank", "PetWidget"]


class PetWidget(QWidget):
    """Frameless, translucent, always-on-top window that renders the current frame."""

    clicked = Signal()
    double_clicked = Signal()
    drag_started = Signal()
    context_requested = Signal(QPoint)
    petted = Signal(str)
    click_through_changed = Signal(bool)

    GRAVITY, BOUNCE, GROUND_FRICTION = 2600.0, 0.32, 0.80
    MAX_PHYSICS_STEP, MAX_ANIMATION_STEP_MS, MAX_THROW_SPEED = 0.05, 50.0, 1800.0

    def __init__(self, sprites: SpriteBank, display_size: int = 168, wardrobe_settings=None):
        super().__init__()
        self.sprites = sprites
        self._original_sprites = sprites
        self._wardrobe_settings = wardrobe_settings
        self.wardrobe_choice = read_wardrobe_choice(wardrobe_settings)
        self.wardrobe_pack = "original"
        self.wardrobe_pack_error = ""
        saved_pack = read_pack_choice(wardrobe_settings)
        if saved_pack != "original":
            try:
                self.sprites = self._load_wardrobe_pack(saved_pack)
                self.wardrobe_pack = saved_pack
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.wardrobe_pack_error = "저장된 외형을 읽지 못해 기본 모습으로 열었어요: " + str(exc)
        self.display_size = display_size
        self.win_width = max(display_size, 300)
        self.base_height = display_size
        self.state = "idle"
        self.frame_idx = 0
        self._facing_left = False
        self.vx = self.vy = 0.0
        self._physics_position: QPointF | None = None
        self._physics_anchor: QPoint | None = None
        self._physics_grounded = False
        self._frame_elapsed = self._transition_elapsed = 0.0
        self._completed_cycles = 0
        self._transition_kind: str | None = None
        self._transition_duration = 180.0
        self.time_scale, self.crossfade, self.BLEND_MS = 1.0, False, 40
        self.motion_amp, self.reduced_motion = 0.8, False
        self._last_tick = time.monotonic()
        self._drag_offset, self._drag_start = QPoint(0, 0), QPoint(0, 0)
        self._dragging, self._drag_started, self._one_shot_done = False, False, False
        self._drag_hist: list[tuple[float, QPoint]] = []
        self._drag_max_distance = 0
        self.bubble_widget: QWidget | None = None
        self.timer_badge_text, self._click_through = "", False

        # Petting (머리 쓰다듬기) & heart_particle system
        self.petting_tracker = PettingTracker()
        self.heart_particles: list[HeartParticle] = []
        # An open hand over the head invites stroking; the behaviour controller
        # turns it off while the onboarding tour owns the pet.
        self.petting_hand_allowed = lambda: True
        self._petting_hand = False
        self.setMouseTracking(True)

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(self.win_width, self.base_height)

        self.tick = QTimer(self)
        self.tick.setInterval(16)
        self.tick.setTimerType(Qt.TimerType.PreciseTimer)
        self.tick.timeout.connect(self._on_tick)
        self.tick.start()

        # Click-through emergency escape hotkey monitor (Ctrl+Alt+S)
        self._escape_timer = QTimer(self)
        self._escape_timer.setInterval(400)
        self._escape_timer.timeout.connect(self._check_click_through_escape)

        self._pm_cache: dict = {}
        self._mask_cache: dict = {}
        self._mask_blend_next: int | None = None

        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.center().x() - self.win_width // 2, geo.bottom() - self.base_height)
        self._update_mask()

    @property
    def facing_left(self) -> bool:
        return self._facing_left

    @facing_left.setter
    def facing_left(self, v: bool):
        v = bool(v)
        if v != self._facing_left:
            self._facing_left = v
            self._update_mask()
            self.update()

    def set_timer_badge(self, text: str) -> None:
        """Update visible timer badge text above the pet (e.g. 🍅 24:50)."""
        new_text = str(text or "")
        if new_text != self.timer_badge_text:
            self.timer_badge_text = new_text
            self._update_mask()
            self.update()

    def _frame_pm(self, idx: int, state: str | None = None, facing_left: bool | None = None) -> QPixmap:
        meta = self._animation_meta(state)
        pm = meta["frames"][idx % len(meta["frames"])]
        pm = decorate_frame(pm, self.wardrobe_choice)
        scale = meta.get("render_scale", 1.0)
        if scale != 1.0:
            canvas = QPixmap(pm.size())
            canvas.fill(Qt.GlobalColor.transparent)
            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            ax, ay = meta["render_anchor"]
            painter.translate(ax, ay)
            painter.scale(scale, scale)
            painter.translate(-ax, -ay)
            painter.drawPixmap(0, 0, pm)
            painter.end()
            pm = canvas
        flip = self.facing_left if facing_left is None else facing_left
        return pm.transformed(QTransform().scale(-1, 1)) if flip else pm

    def set_wardrobe_choice(self, choice: str) -> None:
        choice = write_wardrobe_choice(choice, self._wardrobe_settings)
        if choice != self.wardrobe_choice:
            self.wardrobe_choice = choice
            self._pm_cache.clear()
            self._mask_cache.clear()
            self._mask_blend_next = None
            self._update_mask()
            self.update()

    def _load_wardrobe_pack(self, name: str) -> SpriteBank:
        import re
        from wardrobe_packs import validate_sprite_pack
        if not re.fullmatch(r"pack-[0-9a-f]{32}", name):
            raise ValueError("올바르지 않은 외형 이름입니다.")
        folder = pack_root() / name
        validate_sprite_pack(folder)
        return SpriteBank(folder)

    def set_wardrobe_pack(self, name: str) -> None:
        sprites = self._original_sprites if name == "original" else self._load_wardrobe_pack(name)
        write_pack_choice(name, self._wardrobe_settings)
        interrupted = not self._animation_meta()["loop"]
        self.sprites, self.wardrobe_pack, self.wardrobe_pack_error = sprites, name, ""
        if not self.sprites.has(self.state):
            self.state = "idle"
            self.frame_idx = 0
            self._frame_elapsed = 0.0
            self._completed_cycles = 0
            self._one_shot_done = interrupted
        else:
            self.frame_idx = min(self.frame_idx, len(self._animation_meta()["frames"]) - 1)
        self._frame_elapsed = 0.0
        self._pm_cache.clear()
        self._mask_cache.clear()
        self._mask_blend_next = None
        self._update_mask()
        self._last_tick = time.monotonic()
        self.update()

    def _current_pm(self) -> QPixmap:
        meta = self._animation_meta()
        return self._frame_pm(min(self.frame_idx, len(meta["frames"]) - 1))

    def _animation_meta(self, state: str | None = None) -> dict:
        meta = self.sprites.states[self.state if state is None else state]
        return meta.get("playback", meta)

    def _blend_info(self):
        meta = self._animation_meta()
        n = len(meta["frames"])
        idx = min(self.frame_idx, n - 1)
        dur = meta["durations"][idx % len(meta["durations"])] * self.time_scale
        nxt = ((idx + 1) % n) if meta["loop"] else (idx + 1 if idx + 1 < n else None)
        if self.reduced_motion or self.state == "still" or nxt is None or dur <= 0 or self.BLEND_MS <= 0:
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
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._pm_cache[key] = pm
        return pm

    def _cur_scaled_pm(self) -> QPixmap:
        meta = self._animation_meta()
        return self._scaled_pm(min(self.frame_idx, len(meta["frames"]) - 1))

    def _render_offset_y(self, scaled: QPixmap) -> int:
        meta = self._animation_meta()
        source = meta["frames"][min(self.frame_idx, len(meta["frames"]) - 1)]
        return round(meta.get("render_offset_y", 0) * scaled.height() / source.height())

    def _sprite_draw_rect(self) -> tuple[int, int, int, int]:
        scaled = self._cur_scaled_pm()
        return ((self.win_width - scaled.width()) // 2,
                self.height() - scaled.height() + self._render_offset_y(scaled),
                scaled.width(), scaled.height())

    def _mask_region(self, idx: int) -> QRegion:
        return compute_mask_region(
            self._scaled_pm(idx), self.state, self.facing_left, idx,
            self.motion_amp, self.reduced_motion, self.display_size,
            self._transition_kind, self._mask_cache)

    def _update_mask(self):
        meta = self._animation_meta()
        idx = min(self.frame_idx, len(meta["frames"]) - 1)
        scaled = self._cur_scaled_pm()
        region = self._mask_region(idx)
        nxt, t = self._blend_info() if self.crossfade else (None, 0.0)
        self._mask_blend_next = nxt if t > 0 else None
        if self._mask_blend_next is not None:
            next_scaled = self._scaled_pm(nxt)
            next_region = self._mask_region(nxt)
            next_region.translate((scaled.width() - next_scaled.width()) // 2, scaled.height() - next_scaled.height())
            region |= next_region
        x = (self.win_width - scaled.width()) // 2
        y = self.height() - scaled.height() + self._render_offset_y(scaled)
        region.translate(x, y)
        if self.bubble_widget is not None and self.bubble_widget.isVisible():
            region |= QRegion(self.bubble_widget.geometry())
        if self.timer_badge_text:
            region |= QRegion(self._badge_rect(x, y, scaled.width()))
        self.setMask(region)

    @staticmethod
    def _badge_rect(x: int, y: int, width: int) -> QRect:
        """Focus-timer badge above a sprite drawn at (x, y) with this width."""
        return QRect(x + (width - 88) // 2, max(2, y - 26), 88, 22)

    def _relayout(self):
        floor = self.y() + self.height()
        extra = self.bubble_widget.height() + 4 if (
            self.bubble_widget is not None and self.bubble_widget.isVisible()) else 0
        new_h = self.base_height + extra
        if new_h != self.height():
            self.resize(self.win_width, new_h)
        self.move(self.x(), floor - self.height())
        if self.bubble_widget is not None and self.bubble_widget.isVisible():
            self.bubble_widget.move((self.win_width - self.bubble_widget.width()) // 2, 2)
        self._update_mask()

    def set_reduced_motion(self, enabled: bool) -> None:
        if enabled == self.reduced_motion:
            return
        self.reduced_motion = enabled
        self._last_tick = time.monotonic()
        if enabled:
            self._set_petting_hand(False)
            self.tick.stop()
            self.frame_idx = 0
            self._frame_elapsed = 0.0
            self._transition_kind = None
        else:
            self.tick.start()
        self._update_mask()
        self.update()

    def _on_tick(self):
        now = time.monotonic()
        dt_ms = (now - self._last_tick) * 1000.0
        self._last_tick = now
        durations = self._animation_meta()["durations"]
        max_step = min(self.MAX_ANIMATION_STEP_MS, min(durations) * self.time_scale)
        presented_ms = min(dt_ms, max_step)
        self._advance_frame(presented_ms)
        if self.petting_tracker.particles:
            self.petting_tracker.update_particles(presented_ms / 1000.0)
            self.heart_particles = self.petting_tracker.particles
            self.update()
        if self._transition_kind is not None:
            self._transition_elapsed += max(0.0, presented_ms)
            if self._transition_elapsed >= self._transition_duration:
                self._transition_kind = None
                self._update_mask()
            self.update()
        nxt, t = self._blend_info() if self.crossfade else (None, 0.0)
        blend_next = nxt if t > 0 else None
        if blend_next != self._mask_blend_next:
            self._update_mask()
        if blend_next is not None:
            self.update()

    def set_state(self, state: str):
        if state == self.state or not self.sprites.has(state):
            return
        self.state = state
        self.frame_idx = 0
        self._frame_elapsed = 0.0
        self._completed_cycles = 0
        self._one_shot_done = False
        self._update_mask()
        self._last_tick = time.monotonic()
        self.update()

    def play_transition(self, kind: str, duration_ms: float = 180.0) -> None:
        if (kind not in ("landing", "walk_start", "walk_stop")
                or self.reduced_motion or not math.isfinite(duration_ms) or duration_ms <= 0
                or not math.isfinite(self.motion_amp) or self.motion_amp <= 0):
            return
        self._transition_kind = kind
        self._transition_elapsed = 0.0
        self._transition_duration = duration_ms
        self._update_mask()
        self.update()

    def _cycle_phase(self) -> float:
        meta = self._animation_meta()
        total = sum(meta["durations"]) * self.time_scale
        if total <= 0:
            return 0.0
        passed = sum(meta["durations"][:self.frame_idx]) * self.time_scale + self._frame_elapsed
        return (passed / total) % 1.0

    def _proc_fx(self):
        return compute_proc_fx(
            self.state, self.motion_amp, self.reduced_motion, self.facing_left,
            self._cycle_phase(), self._transition_kind,
            self._transition_elapsed, self._transition_duration)

    def _advance_frame(self, dt_ms: float):
        if (self.reduced_motion or self.state == "still" or not math.isfinite(dt_ms) or dt_ms <= 0
                or not math.isfinite(self.time_scale) or self.time_scale <= 0):
            return
        meta = self._animation_meta()
        if self._one_shot_done and not meta["loop"]:
            return
        self._frame_elapsed += dt_ms
        durations = meta["durations"]
        if meta["loop"]:
            cycle = sum(durations[i % len(durations)] for i in range(len(meta["frames"]))) * self.time_scale
            if cycle > 0 and self._frame_elapsed + 1e-7 >= cycle:
                cycles = int((self._frame_elapsed + 1e-7) // cycle)
                self._completed_cycles += cycles
                self._frame_elapsed = max(0.0, self._frame_elapsed - cycles * cycle)
        changed = False
        while self._frame_elapsed + 1e-7 >= durations[self.frame_idx % len(durations)] * self.time_scale:
            self._frame_elapsed = max(0.0, self._frame_elapsed - durations[self.frame_idx % len(durations)] * self.time_scale)
            self.frame_idx += 1
            changed = True
            if self.frame_idx >= len(meta["frames"]):
                if meta["loop"]:
                    self.frame_idx = 0
                    self._completed_cycles += 1
                else:
                    self.frame_idx = len(meta["frames"]) - 1
                    self._frame_elapsed = 0.0
                    self._one_shot_done = True
                    break
        if changed:
            self._update_mask()
            self.update()
        elif self.motion_amp > 0 and self.state in ("walk", "idle", "dance", "sleep", "liesleep", "drag"):
            self.update()

    def one_shot_finished(self) -> bool:
        return self._one_shot_done

    def _screen_at_pet(self):
        """Screen under the pet center; nearest screen when in a monitor gap."""
        center = self.geometry().center()
        screen = QGuiApplication.screenAt(center)
        if screen is not None:
            return screen
        cx = center.x()
        best, best_d = None, None
        for s in QGuiApplication.screens():
            g = s.availableGeometry()
            d = 0 if g.left() <= cx <= g.right() else min(abs(cx - g.left()), abs(cx - g.right()))
            if best_d is None or d < best_d:
                best, best_d = s, d
        return best or QGuiApplication.primaryScreen()

    def ground_y(self) -> int:
        """Floor on the monitor the pet is currently standing on."""
        return self._screen_at_pet().availableGeometry().bottom()

    def screen_bounds(self):
        """Roam across monitors only when more than one is actually attached."""
        screens = QGuiApplication.screens()
        if len(screens) > 1:
            bounds = QRect()
            for s in screens:
                bounds = bounds.united(s.availableGeometry())
            return bounds
        return self._screen_at_pet().availableGeometry()

    def current_screen_bounds(self):
        """Work area of just the monitor under the pet (anchors, y clamping)."""
        return self._screen_at_pet().availableGeometry()

    def sprite_rect_global(self):
        x, y, w, h = self._sprite_draw_rect()
        return self.geometry().adjusted(x, y, x + w - self.width(), y + h - self.height())

    def physics_step(self, dt: float) -> bool:
        return integrate_physics_step(self, dt)

    def _set_petting_hand(self, shown: bool) -> None:
        if shown == self._petting_hand:
            return
        self._petting_hand = shown
        if shown:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def leaveEvent(self, e):
        self._set_petting_hand(False)
        super().leaveEvent(e)

    def mousePressEvent(self, e: QMouseEvent):
        self._set_petting_hand(False)
        if e.button() == Qt.MouseButton.LeftButton:
            self._physics_position = None
            self._physics_anchor = None
            self._physics_grounded = False
            self._dragging = True
            self._drag_start = e.globalPosition().toPoint()
            self._drag_max_distance = 0
            self._drag_started = False
            self._drag_offset = self._drag_start - self.pos()
            self._drag_hist = [(time.monotonic(), self._drag_start)]
            self.vx = self.vy = 0.0

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._dragging:
            gp = e.globalPosition().toPoint()
            self._drag_max_distance = max(self._drag_max_distance, (gp - self._drag_start).manhattanLength())
            if self._drag_max_distance >= 8:
                self._begin_drag()
                self.move(gp - self._drag_offset)
            self._drag_hist.append((time.monotonic(), gp))
            self._drag_hist = self._drag_hist[-6:]
        elif not self.reduced_motion:
            pos = e.position()
            px, py = pos.x(), pos.y()
            rx, ry, rw, rh = self._sprite_draw_rect()
            # The focus-timer badge sits inside the sprite box above the leaf;
            # it is not Syeon's head, so it neither shows the hand nor counts.
            inside = (rx <= px <= rx + rw and ry <= py <= ry + rh
                      and not (self.timer_badge_text and self._badge_rect(rx, ry, rw).contains(int(px), int(py))))
            self._set_petting_hand(inside and py - ry < rh * HEAD_ZONE_RATIO
                                   and self.petting_hand_allowed())
            if inside:
                triggered, line = self.petting_tracker.feed_point(px, py - ry, rh)
                if triggered:
                    for pt in self.petting_tracker.particles[-7:]:
                        pt.y += ry
                    self.heart_particles = self.petting_tracker.particles
                    self.petted.emit(line or "")
                    self.update()
        else:
            self._set_petting_hand(False)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if e.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        gp = e.globalPosition().toPoint()
        self._drag_max_distance = max(self._drag_max_distance, (gp - self._drag_start).manhattanLength())
        if self._drag_max_distance < 8:
            self._dragging = False
            self.vx = self.vy = 0.0
            self.clicked.emit()
        else:
            self._begin_drag()
            self.move(gp - self._drag_offset)
            self._dragging = False
            self._drag_hist.append((time.monotonic(), gp))
            self._drag_hist = self._drag_hist[-6:]
            (t0, p0), (t1, p1) = self._drag_hist[0], self._drag_hist[-1]
            dt = max(t1 - t0, 1e-3)
            self.vx = max(-self.MAX_THROW_SPEED, min(self.MAX_THROW_SPEED, (p1.x() - p0.x()) / dt))
            self.vy = max(-self.MAX_THROW_SPEED, min(self.MAX_THROW_SPEED, (p1.y() - p0.y()) / dt))

    def _begin_drag(self):
        if not self._drag_started:
            self._drag_started = True
            self.drag_started.emit()

    def mouseDoubleClickEvent(self, e: QMouseEvent):
        self.double_clicked.emit()

    def contextMenuEvent(self, e):
        self.context_requested.emit(e.globalPos())
        e.accept()

    def _draw_scaled(self, p: QPainter, scaled: QPixmap):
        p.drawPixmap((self.win_width - scaled.width()) // 2,
                     self.height() - scaled.height() + self._render_offset_y(scaled), scaled)

    def _crossfaded_pixmap(self, idx: int, nxt: int, t: float) -> QPixmap:
        return blend_pixmaps(self._scaled_pm(idx), self._scaled_pm(nxt), t)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        sx, sy, rot, dx, dy = self._proc_fx()
        if sx != 1.0 or sy != 1.0 or rot or dx or dy:
            rx, ry, rw, rh = self._sprite_draw_rect()
            p.translate(rx + rw / 2 + dx, ry + rh + dy)
            p.rotate(rot)
            p.scale(sx, sy)
            p.translate(-rx - rw / 2, -ry - rh)
        meta = self._animation_meta()
        idx = min(self.frame_idx, len(meta["frames"]) - 1)
        nxt, t = self._blend_info() if self.crossfade else (None, 0.0)
        frame = blend_pixmaps(self._scaled_pm(idx), self._scaled_pm(nxt), t) if nxt is not None and t > 0 else self._scaled_pm(idx)
        p.drawPixmap((self.win_width - frame.width()) // 2,
                     self.height() - frame.height() + self._render_offset_y(frame), frame)
        if self.heart_particles:
            paint_hearts(p, self.heart_particles)
        if self.timer_badge_text:
            rx, ry, rw, rh = self._sprite_draw_rect()
            TimerBadge.paint(p, self._badge_rect(rx, ry, rw), self.timer_badge_text)
        p.end()

    def showEvent(self, e):
        super().showEvent(e)
        self._update_mask()

    def set_click_through(self, enabled: bool):
        """Toggle whole-window input passing with safety escape monitoring."""
        self._click_through = bool(enabled)
        if self._click_through:
            self._set_petting_hand(False)
        if sys.platform == "win32":
            import ctypes
            hwnd = int(self.winId())
            gwl_exstyle = -20
            WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x00080000, 0x00000020
            style = ctypes.windll.user32.GetWindowLongPtrW(hwnd, gwl_exstyle)
            style = (style | WS_EX_LAYERED | WS_EX_TRANSPARENT) if self._click_through else (style & ~WS_EX_TRANSPARENT)
            ctypes.windll.user32.SetWindowLongPtrW(hwnd, gwl_exstyle, style)
            if self._click_through:
                self._escape_timer.start()
            else:
                self._escape_timer.stop()
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
            if enabled:
                self.clearFocus()
        self.click_through_changed.emit(self._click_through)

    def restore_click_through(self) -> None:
        """Safety escape: restores normal click reception."""
        self.set_click_through(False)

    def _check_click_through_escape(self) -> None:
        """Emergency click_through_escape hotkey poller checking Ctrl+Alt+S."""
        if not self._click_through or sys.platform != "win32":
            return
        try:
            import ctypes
            ctrl = ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000
            alt = ctypes.windll.user32.GetAsyncKeyState(0x12) & 0x8000
            s_key = ctypes.windll.user32.GetAsyncKeyState(0x53) & 0x8000
            if ctrl and alt and s_key:
                self.restore_click_through()
        except Exception:
            pass
