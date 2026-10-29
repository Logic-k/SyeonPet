"""Behavior state machine — wander / idle / sleep / drag-fall / one-shot actions."""
from __future__ import annotations

import random
import time
import math

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from pet import PetWidget


class BehaviorController(QObject):
    """Drives the pet: picks goals, moves it, plays matching sprite states.

    States: wander -> idle -> (sleep) cycle, interrupted by drag/fall,
    wave, celebrate, and the onboarding tour (which owns the pet while active).
    """

    WALK_SPEED = 190  # px/s
    WALK_SPEED_SLOW = 80
    THINKING_MIN_SECONDS = 0.8
    fully_asleep_changed = Signal(bool)

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
        self._fully_asleep = False
        self._pre_fall_mode = "idle"
        self._pre_drag_state = "idle"
        # A request may be interrupted by a user action.  Only restore the
        # saved state while this controller still owns the thinking pose.
        self._thinking_restore: dict | None = None
        self._thinking_started = 0.0
        self._thinking_finish_pending = False
        self._thinking_generation = 0
        # gesture -> action name mapping (see run_action table)
        self.click_actions = {"click": "wave", "double_click": "sleep"}

        self.pet.clicked.connect(self._on_clicked)
        self.pet.double_clicked.connect(self._on_double_clicked)
        self.pet.drag_started.connect(self._on_drag_started)

        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # ---------- public ----------

    def begin_thinking(self):
        """Show a temporary pose while a prompt request is running.

        Completely still sleep blocks requests; lighter sleep can briefly
        show the pose and is restored afterwards. Never take over a drag/tour.
        Calling it twice for one request is harmless.
        """
        if self._thinking_restore is not None:
            if self._thinking_finish_pending:
                # A new request began before the short visual tail finished.
                # Keep the original snapshot, not the transient thinking pose.
                self._thinking_generation += 1
                self._thinking_finish_pending = False
                self._thinking_started = time.monotonic()
            return
        if (self.onboarding_active or self.fully_asleep or self._fell
                or self.pet._dragging or self.mode in ("drag", "fall")):
            return
        now = time.monotonic()
        self._thinking_started = now
        self._thinking_finish_pending = False
        self._thinking_restore = {
            "mode": self.mode,
            "state": self.pet.state,
            "remaining": max(0.0, self.mode_until - now),
            "idle_ticks": self.idle_ticks,
            "target_x": self.target_x,
            "walk_accum": self._walk_accum,
            "frame_idx": self.pet.frame_idx,
            "frame_elapsed": self.pet._frame_elapsed,
            "one_shot_done": self.pet._one_shot_done,
        }
        self.mode = "thinking"
        self.pet.set_state("thinking" if self.pet.sprites.has("thinking") else "idle")

    def end_thinking(self):
        """Leave a brief visual tail without delaying delivery of the result."""
        if self._thinking_restore is None or self._thinking_finish_pending:
            return
        elapsed = max(0.0, time.monotonic() - self._thinking_started)
        remaining = self.THINKING_MIN_SECONDS - elapsed
        if remaining > 0:
            self._thinking_finish_pending = True
            self._thinking_generation += 1
            generation = self._thinking_generation
            QTimer.singleShot(max(1, math.ceil(remaining * 1000)),
                              lambda: self._finish_thinking(generation))
            return
        self._finish_thinking()

    def _finish_thinking(self, generation=None):
        """Restore only if the request still owns the pose."""
        if generation is not None and (generation != self._thinking_generation
                                       or not self._thinking_finish_pending):
            return
        self._thinking_generation += 1
        self._thinking_finish_pending = False
        saved, self._thinking_restore = self._thinking_restore, None
        if (saved is None or self.mode != "thinking" or self.onboarding_active
                or self._fell or self.pet._dragging or self.fully_asleep):
            return
        self.mode = saved["mode"]
        self.mode_until = time.monotonic() + saved["remaining"]
        self.idle_ticks = saved["idle_ticks"]
        self.target_x = saved["target_x"]
        self._walk_accum = saved["walk_accum"]
        self.pet.set_state(saved["state"])
        self.pet.frame_idx = saved["frame_idx"]
        self.pet._frame_elapsed = saved["frame_elapsed"]
        self.pet._one_shot_done = saved["one_shot_done"]
        self.pet._update_mask()
        self.pet.update()

    def _interrupt_thinking(self):
        """Forget the snapshot when the user explicitly chooses a new action."""
        self._thinking_generation += 1
        self._thinking_finish_pending = False
        self._thinking_restore = None

    @property
    def fully_asleep(self) -> bool:
        """The explicit motionless sleep mode, including while being dragged."""
        return self._fully_asleep

    @property
    def sleeping(self) -> bool:
        """Whether the current or temporarily interrupted state is sleep."""
        if self.fully_asleep or self.mode in ("sleep", "deepsleep"):
            return True
        if self.mode == "thinking" and self._thinking_restore is not None:
            return self._thinking_restore["mode"] in ("sleep", "deepsleep")
        if self.mode in ("drag", "fall") and self._fell:
            return self._pre_fall_mode in ("sleep", "deepsleep")
        return False

    def _set_fully_asleep(self, asleep: bool):
        if self._fully_asleep != asleep:
            self._fully_asleep = asleep
            self.fully_asleep_changed.emit(asleep)

    def wake(self):
        """Wake explicitly without erasing either conversation's draft."""
        if self.sleeping:
            self._pre_fall_mode = "idle"
            self.idle(3.0)

    def toggle_sleep(self):
        """Honor wake requests even when thinking briefly hides sleep."""
        if self.sleeping:
            self._pre_fall_mode = "idle"
            self.idle(1.0)
        else:
            self.sleep()

    def set_slow(self, on: bool):
        """Calm mode: slower walk, slower sprite playback, longer rests."""
        self.slow = on
        self.WALK_SPEED = self.WALK_SPEED_SLOW if on else self.base_walk_speed
        self.pet.time_scale = 1.7 if on else 1.0
        if self.mode == "idle":
            self.idle()

    def wander(self, *, reset_count: bool = True):
        self._interrupt_thinking()
        self._set_fully_asleep(False)
        if reset_count:
            self.idle_ticks = 0
        bounds = self.pet.screen_bounds()
        self.target_x = random.randint(bounds.left() + 10,
                                       bounds.right() - self.pet.width() - 10)
        self.mode = "walk"
        self.pet.set_state("walk")

    def idle(self, seconds: float = 0.0, *, reset_count: bool = True):
        self._interrupt_thinking()
        self._set_fully_asleep(False)
        self.mode = "idle"
        if reset_count:
            self.idle_ticks = 0
        self.pet.set_state("idle")
        rest = seconds or (random.uniform(9.0, 16.0) if self.slow
                           else random.uniform(2.5, 6.0))
        self.mode_until = time.monotonic() + rest

    def sleep(self):
        self._interrupt_thinking()
        self._set_fully_asleep(False)
        self.mode = "sleep"
        self.idle_ticks = 0
        self.pet.set_state("sleep")
        self.mode_until = time.monotonic() + 45.0

    def deep_sleep(self):
        """Lying-down sleep — stays put, no wander, until woken."""
        self._interrupt_thinking()
        self._set_fully_asleep(False)
        self.mode = "deepsleep"
        self.idle_ticks = 0
        self._deep_state = "liesleep"
        self.pet.set_state("liesleep")

    def still_sleep(self):
        """Completely motionless flop — nothing moves until woken."""
        self._interrupt_thinking()
        self.mode = "deepsleep"
        self.idle_ticks = 0
        self._deep_state = "still"
        self.pet.set_state("still")
        self._set_fully_asleep(True)

    def dance(self, seconds: float = 2.4):
        self._interrupt_thinking()
        self._set_fully_asleep(False)
        self.mode = "dance"
        self.pet.set_state("dance")
        self.mode_until = time.monotonic() + seconds

    def play_one_shot(self, state: str, then: str = "idle"):
        """wave / celebrate; returns to `then` when the strip finishes."""
        self._interrupt_thinking()
        self._set_fully_asleep(False)
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

    def _on_drag_started(self):
        # This can arrive before the next timer tick, including a drag whose
        # press, move, and release all happen between two ticks.
        if self.mode != "drag":
            if self.mode == "thinking":
                # A direct gesture takes precedence over an in-flight request.
                # Do not resume a stale thinking state after landing.
                self._interrupt_thinking()
                self.mode = "idle"
                self.pet.set_state("idle")
            self._pre_fall_mode = self.mode
            self._pre_drag_state = self.pet.state
            self.mode = "drag"
            self.pet.set_state("drag")
        self._fell = True

    def _on_clicked(self):
        if self.onboarding_active:
            return
        self.idle_ticks = 0
        # A normal press may last longer than one timer tick.  _tick then
        # temporarily sets drag even when the pointer never actually moved;
        # clicked means mouseReleaseEvent has classified it as a click.
        if self.mode == "drag" and self._fell:
            self.mode = self._pre_fall_mode
            if self.mode != "fall":
                self._fell = False
                self.pet.set_state(self._pre_drag_state)
        if self.fully_asleep or self.mode in ("sleep", "deepsleep"):
            self._pre_fall_mode = "idle"
            self.idle(3.0)
            self.play_one_shot("wave")
        elif self.mode not in ("drag", "fall"):
            self.run_action(self.click_actions.get("click", "wave"))

    def _on_double_clicked(self):
        if not self.onboarding_active:
            self.idle_ticks = 0
            self.run_action(self.click_actions.get("double_click", "sleep"))

    # ---------- tick ----------

    def _tick(self):
        now = time.monotonic()
        # A suspended window or system resume must not teleport the pet.
        dt = max(0.0, min(now - self._last_tick_time, 0.05))
        self._last_tick_time = now
        pet = self.pet

        if pet._dragging:
            self._on_drag_started()
            return

        if self._fell:
            if self.mode != "fall":
                self.mode = "fall"
                pet.set_state("drag")
            if pet.physics_step(dt):
                self._fell = False
                prev = self._pre_fall_mode
                if self.fully_asleep:
                    self.still_sleep()
                elif prev == "sleep":
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
                    self.idle(reset_count=False)
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
                self.wander(reset_count=False)

        elif self.mode == "dance" and now >= self.mode_until:
            self.idle()

        elif self.mode == "sleep" and now >= self.mode_until:
            self.deep_sleep()

        elif self.mode.startswith("oneshot:") and pet.one_shot_finished():
            nxt = self.mode.split(":", 1)[1]
            if nxt == "idle":
                self.idle(reset_count=False)
            elif nxt == "wander":
                self.wander(reset_count=False)
            else:
                self.sleep()
