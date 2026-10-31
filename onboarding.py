"""Onboarding tour — the pet walks to anchor spots and narrates steps."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, Qt, QTimer

from behavior import BehaviorController
from bubble import SpeechBubble
from pet import PetWidget


class OnboardingTour(QObject):
    """Scripted walkthrough: pet ambles between screen anchors while the
    bubble (a child of the pet window) shows each step. The behavior machine
    is suspended meanwhile."""

    def __init__(self, pet: PetWidget, behavior: BehaviorController,
                 bubble: SpeechBubble, steps: list[dict]):
        super().__init__()
        self.pet = pet
        self.behavior = behavior
        self.bubble = bubble
        self.steps = steps
        self.index = -1
        self._arrived = False
        self._last_tick_time = time.monotonic()
        self._movement_paused = False

        bubble.next_clicked.connect(self.next)
        bubble.prev_clicked.connect(self.prev)
        bubble.skip_clicked.connect(self.finish)

        self._tick = QTimer(self)
        self._tick.setInterval(30)
        self._tick.setTimerType(Qt.TimerType.PreciseTimer)
        self._tick.timeout.connect(self._update)

    @property
    def active(self) -> bool:
        return self.index >= 0

    # ---------- flow ----------

    def start(self):
        if self.active or not self.steps:
            return
        self.behavior.onboarding_active = True
        self.index = 0
        self._arrived = False
        self._last_tick_time = time.monotonic()
        self.behavior.reset_walk()
        self._movement_paused = self.pet._dragging or self.behavior._fell
        if not self._movement_paused:
            if self.pet.reduced_motion:
                self.behavior._set_pose("idle", "idle")
            else:
                self.pet.facing_left = self._anchor_x(self.steps[0]) < self.pet.x()
                self.behavior._set_pose("walk", "walk", bridge="turn_bridge")
                self.pet.play_transition("walk_start")
        if not self.pet.reduced_motion:
            self._tick.start()
        self.bubble.show_step(self._step_text(0), 0, len(self.steps), True)
        self.pet._relayout()

    def next(self):
        if self.index >= len(self.steps) - 1:
            self.finish()
            return
        self.index += 1
        self._arrived = False
        self.bubble.show_step(self._step_text(self.index), self.index,
                              len(self.steps), True)
        self.pet._relayout()

    def prev(self):
        if self.index <= 0:
            return
        self.index -= 1
        self._arrived = False
        self.bubble.show_step(self._step_text(self.index), self.index,
                              len(self.steps), True)
        self.pet._relayout()

    def finish(self):
        if not self.active:
            return
        self.index = -1
        self._tick.stop()
        self.bubble.hide()
        self.pet._relayout()
        self.behavior.onboarding_active = False
        self.behavior.reset_walk()
        if self.pet.reduced_motion:
            self.behavior.idle()
        elif not self.pet._dragging and not self.behavior._fell:
            self.behavior.play_one_shot("celebrate", then="idle")

    # ---------- internals ----------

    def refresh_motion_policy(self):
        if not self.active:
            return
        self._arrived = False
        self._last_tick_time = time.monotonic()
        self.behavior.reset_walk()
        if self.pet.reduced_motion:
            self._tick.stop()
            self.behavior._set_pose("idle", "idle")
        else:
            self._tick.start()

    def _step_text(self, i: int) -> str:
        s = self.steps[i]
        body = s.get("text", "")
        if s.get("title"):
            body = f"<b>{s['title']}</b><br>{body}"
        return body + f"<br><span style='color:#9a8f84'>{i + 1} / {len(self.steps)}</span>"

    def _anchor_x(self, step: dict) -> int:
        bounds = self.pet.screen_bounds()
        anchor = step.get("anchor", "center")
        if anchor == "left":
            return bounds.left() + bounds.width() // 5
        if anchor == "right":
            return bounds.right() - bounds.width() // 5 - self.pet.width()
        return bounds.center().x() - self.pet.width() // 2

    def _update(self):
        if not self.active:
            return
        now = time.monotonic()
        dt = max(0.0, min(now - self._last_tick_time, 0.05))
        self._last_tick_time = now
        if self.pet._dragging or self.behavior._fell:
            self._movement_paused = True
            self.behavior.reset_walk()
            return
        if self._movement_paused:
            self._movement_paused = False
            self._arrived = False
            dt = 0.0
        if self.pet.reduced_motion:
            self._tick.stop()
            self.behavior._set_pose("idle", "idle")
            self._arrived = True
            self.pet._relayout()
            return
        if not self.behavior.advance_transition():
            self.pet._relayout()
            return
        target = self._anchor_x(self.steps[self.index])
        if not self._arrived:
            if self.behavior.move_toward(target, dt):
                self._arrived = True
                state = "wave" if self.index == 0 else "idle"
                mode = "oneshot:idle" if state == "wave" else "idle"
                self.behavior._set_pose(mode, state, bridge="turn_front")
                self.pet.play_transition("walk_stop")
            elif self.pet.state != "walk" and not self.behavior.mode.startswith("transition:"):
                self.pet.facing_left = target < self.pet.x()
                self.behavior._set_pose("walk", "walk", bridge="turn_bridge")
                self.pet.play_transition("walk_start")
        elif self.pet.state == "wave" and self.pet.one_shot_finished():
            self.behavior._set_pose("idle", "idle")
        self.pet._relayout()
