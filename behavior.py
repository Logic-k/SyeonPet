"""Behavior state machine — wander / idle / sleep / drag-fall / one-shot actions."""
from __future__ import annotations

import random
import time

from PySide6.QtCore import QObject, Qt, QTimer

from pet import PetWidget


class BehaviorController(QObject):
    """Drives the pet: picks goals, moves it, plays matching sprite states.

    States: wander -> idle -> (sleep) cycle, interrupted by drag/fall,
    wave, celebrate, and the onboarding tour (which owns the pet while active).
    """

    WALK_SPEED = 190  # px/s
    WALK_SPEED_SLOW = 80

    def __init__(self, pet: PetWidget):
        super().__init__()
        self.pet = pet
        self.mode = "idle"
        self.slow = False
        self.base_walk_speed = self.WALK_SPEED
        self.target_x = 0
        self.mode_until = time.monotonic() + 2.0
        self.idle_ticks = 0
        self.onboarding_active = False
        self._fell = False
        self._last_tick_time = time.monotonic()
        self._walk_accum = 0.0
        self._deep_state = "liesleep"
        self._pre_fall_mode = "idle"
        # gesture -> action name mapping (see run_action table)
        self.click_actions = {"click": "wave", "double_click": "sleep"}

        self.pet.clicked.connect(self._on_clicked)
        self.pet.double_clicked.connect(self._on_double_clicked)

        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # ---------- public ----------

    def set_slow(self, on: bool):
        """Calm mode: slower walk, slower sprite playback, longer rests."""
        self.slow = on
        self.WALK_SPEED = self.WALK_SPEED_SLOW if on else self.base_walk_speed
        self.pet.time_scale = 1.7 if on else 1.0
        if self.mode == "idle":
            self.idle()

    def wander(self):
        bounds = self.pet.screen_bounds()
        self.target_x = random.randint(bounds.left() + 10,
                                       bounds.right() - self.pet.width() - 10)
        self.mode = "walk"
        self.pet.set_state("walk")

    def idle(self, seconds: float = 0.0):
        self.mode = "idle"
        self.idle_ticks = 0
        self.pet.set_state("idle")
        rest = seconds or (random.uniform(9.0, 16.0) if self.slow
                           else random.uniform(2.5, 6.0))
        self.mode_until = time.monotonic() + rest

    def sleep(self):
        self.mode = "sleep"
        self.idle_ticks = 0
        self.pet.set_state("sleep")
        self.mode_until = time.monotonic() + 45.0

    def deep_sleep(self):
        """Lying-down sleep — stays put, no wander, until woken."""
        self.mode = "deepsleep"
        self.idle_ticks = 0
        self._deep_state = "liesleep"
        self.pet.set_state("liesleep")

    def still_sleep(self):
        """Completely motionless flop — nothing moves until woken."""
        self.mode = "deepsleep"
        self.idle_ticks = 0
        self._deep_state = "still"
        self.pet.set_state("still")

    def dance(self, seconds: float = 2.4):
        self.mode = "dance"
        self.pet.set_state("dance")
        self.mode_until = time.monotonic() + seconds

    def play_one_shot(self, state: str, then: str = "idle"):
        """wave / celebrate; returns to `then` when the strip finishes."""
        self.mode = f"oneshot:{then}"
        self.pet.set_state(state)

    def run_action(self, name: str):
        table = {
            "wave": lambda: self.play_one_shot("wave"),
            "celebrate": lambda: self.play_one_shot("celebrate"),
            "jump": lambda: self.play_one_shot("jump"),
            "dance": self.dance,
            "sleep": self.sleep,
            "liesleep": self.deep_sleep,
            "still": self.still_sleep,
            "idle": self.idle,
            "wander": self.wander,
        }
        fn = table.get(name)
        if fn:
            fn()

    # ---------- events ----------

    def _on_clicked(self):
        if self.onboarding_active:
            return
        if self.mode in ("sleep", "deepsleep"):
            self.idle(3.0)
            self.play_one_shot("wave")
        elif self.mode not in ("drag", "fall"):
            self.run_action(self.click_actions.get("click", "wave"))

    def _on_double_clicked(self):
        if not self.onboarding_active:
            self.run_action(self.click_actions.get("double_click", "sleep"))

    # ---------- tick ----------

    def _tick(self):
        now = time.monotonic()
        dt = now - self._last_tick_time
        self._last_tick_time = now
        pet = self.pet

        if pet._dragging:
            if self.mode != "drag":
                self.mode = "drag"
                pet.set_state("drag")
            self._fell = True
            return

        if self._fell:
            if self.mode != "fall":
                self._pre_fall_mode = self.mode
                self.mode = "fall"
                pet.set_state("drag")
            if pet.physics_step(dt):
                self._fell = False
                prev = self._pre_fall_mode
                if prev == "sleep":
                    self.sleep()
                elif prev == "deepsleep":
                    self.mode = "deepsleep"
                    pet.set_state(self._deep_state)
                else:
                    self.idle(1.5)
            return

        if self.onboarding_active:
            return

        if self.mode == "walk":
            dx = self.target_x - pet.x()
            self._walk_accum += self.WALK_SPEED * dt
            step = int(self._walk_accum)
            self._walk_accum -= step
            if abs(dx) <= step + 1:
                pet.move(self.target_x, pet.y())
                self._walk_accum = 0.0
                if random.random() < 0.2:
                    self.play_one_shot("jump")
                else:
                    self.idle()
            elif step:
                pet.facing_left = dx < 0
                pet.move(pet.x() + step * (1 if dx > 0 else -1), pet.y())

        elif self.mode == "idle" and now >= self.mode_until:
            self.idle_ticks += 1
            if self.idle_ticks >= 4 and random.random() < 0.45:
                self.idle_ticks = 0
                self.sleep()
            elif random.random() < 0.18:
                self.play_one_shot("jump")
            else:
                self.wander()

        elif self.mode == "dance" and now >= self.mode_until:
            self.idle()

        elif self.mode == "sleep" and now >= self.mode_until:
            self.deep_sleep()

        elif self.mode.startswith("oneshot:") and pet.one_shot_finished():
            nxt = self.mode.split(":", 1)[1]
            {"idle": self.idle, "wander": self.wander, "sleep": self.sleep}[nxt]()
