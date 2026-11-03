"""Behavior state machine — wander / idle / sleep / drag-fall / one-shot actions."""
from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from pet import PetWidget
from syeonpet.pet.behavior_bridges import (
    calculate_walk_displacement,
    calculate_wander_target,
    resolve_deep_sleep_start_frame,
    resolve_sleep_bridge,
    resolve_wake_start_frame,
    restore_thinking_snapshot,
)


class BehaviorController(QObject):
    """Drives the pet: picks goals, moves it, plays matching sprite states.

    States: wander -> idle -> (sleep) cycle, interrupted by drag/fall,
    wave, celebrate, and the onboarding tour (which owns the pet while active).
    """

    WALK_SPEED, WALK_SPEED_SLOW, WALK_RAMP_SECONDS = 190, 80, 0.25
    THINKING_MIN_SECONDS = 0.8
    AUTO_NAP_SECONDS, AUTO_NAP_SECONDS_SLOW = (60.0, 120.0), (120.0, 240.0)

    fully_asleep_changed = Signal(bool)
    hub_requested = Signal()

    def __init__(self, pet: PetWidget, click_actions: dict | None = None):
        super().__init__()
        self.pet = pet
        self.mode, self.slow = "idle", False
        self.base_walk_speed = self.WALK_SPEED
        self.target_x, self.idle_ticks = 0, 0
        self.mode_until = time.monotonic() + 2.0
        self.onboarding_active, self._fell = False, False
        self._last_tick_time = time.monotonic()
        self._walk_accum, self._walk_velocity = 0.0, 0.0
        self._dance_cycle_target = 0
        self._transition_to: dict | None = None
        self._deep_state, self._fully_asleep, self._auto_nap = "liesleep", False, False
        self._pre_fall_mode, self._pre_drag_state = "idle", "idle"
        self._thinking_restore: dict | None = None
        self._thinking_started = 0.0
        self._thinking_finish_pending, self._thinking_generation = False, 0

        # Preserve exact default click actions regression invariant
        if click_actions is None:
            self.click_actions = {"click": "wave", "single_click": "wave", "double_click": "sleep"}
        else:
            self.click_actions = dict(click_actions)
            if "click" not in self.click_actions and "single_click" in self.click_actions:
                self.click_actions["click"] = self.click_actions["single_click"]

        self.pet.clicked.connect(self._on_clicked)
        self.pet.double_clicked.connect(self._on_double_clicked)
        self.pet.drag_started.connect(self._on_drag_started)

        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def begin_thinking(self):
        """Show a temporary pose while a prompt request is running."""
        if self._thinking_restore is not None:
            if self._thinking_finish_pending:
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
            "walk_velocity": self._walk_velocity,
            "completed_cycles": self.pet._completed_cycles,
            "dance_cycle_target": self._dance_cycle_target,
            "transition_to": self._transition_to.copy() if self._transition_to else None,
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
            gen = self._thinking_generation
            QTimer.singleShot(max(1, math.ceil(remaining * 1000)), lambda: self._finish_thinking(gen))
            return
        self._finish_thinking()

    def _finish_thinking(self, generation=None):
        if generation is not None and (generation != self._thinking_generation or not self._thinking_finish_pending):
            return
        self._thinking_generation += 1
        self._thinking_finish_pending = False
        saved, self._thinking_restore = self._thinking_restore, None
        if (saved is None or self.mode != "thinking" or self.onboarding_active
                or self._fell or self.pet._dragging or self.fully_asleep):
            return
        restore_thinking_snapshot(self, saved)

    def _interrupt_thinking(self):
        self._thinking_generation += 1
        self._thinking_finish_pending = False
        self._thinking_restore = None
        self._transition_to = None

    @property
    def fully_asleep(self) -> bool:
        return self._fully_asleep

    @property
    def sleeping(self) -> bool:
        if self.fully_asleep or self.mode in ("sleep", "deepsleep"):
            return True
        if self.mode.startswith("transition:") and self._transition_to:
            return self._transition_to["mode"] in ("sleep", "deepsleep")
        if self.mode == "thinking" and self._thinking_restore is not None:
            saved = self._thinking_restore
            target = saved.get("transition_to") or saved
            return target["mode"] in ("sleep", "deepsleep")
        if self.mode in ("drag", "fall") and self._fell:
            return self._pre_fall_mode in ("sleep", "deepsleep")
        return False

    def _set_fully_asleep(self, asleep: bool):
        if self._fully_asleep != asleep:
            self._fully_asleep = asleep
            self.fully_asleep_changed.emit(asleep)

    def wake(self):
        if self.sleeping:
            self._pre_fall_mode = "idle"
            self.idle(3.0)

    def toggle_sleep(self):
        if self.sleeping:
            self._pre_fall_mode = "idle"
            self.idle(1.0)
        else:
            self.sleep()

    def set_slow(self, on: bool):
        self.slow = on
        self.WALK_SPEED = self.WALK_SPEED_SLOW if on else self.base_walk_speed
        self.pet.time_scale = 1.7 if on else 1.0
        if self.mode == "idle":
            self.idle()

    def reset_walk(self):
        self._walk_accum = 0.0
        self._walk_velocity = 0.0

    def _wake_start_frame(self) -> int:
        return resolve_wake_start_frame(self.pet.state, self.pet.frame_idx, self._thinking_restore)

    def _set_pose(self, mode: str, state: str, *, seconds: float | None = None,
                  bridge: str | None = None, start_frame: int = 0, end_frame: int | None = None):
        self._transition_to = None
        if (bridge and self.pet.sprites.has(bridge) and not self.pet.reduced_motion
                and not self.pet._dragging and not self._fell):
            self._transition_to = {"mode": mode, "state": state, "seconds": seconds, "end_frame": end_frame}
            self.mode = f"transition:{mode}"
            if self.pet.state == bridge:
                self.pet.set_state("idle")
            self.pet.set_state(bridge)
            self.pet.frame_idx = min(start_frame, len(self.pet._animation_meta()["frames"]) - 1)
            self.pet._frame_elapsed = 0.0
            self.pet._update_mask()
            self.pet.update()
        else:
            self.mode = mode
            self.pet.set_state(state)
        if seconds is not None:
            self.mode_until = time.monotonic() + seconds

    def _finish_transition(self):
        destination, self._transition_to = self._transition_to, None
        if destination is None:
            self.idle(reset_count=False)
            return
        if "facing_left" in destination:
            self.pet.facing_left = destination["facing_left"]
        if destination.get("next_bridge") and self.pet.sprites.has(destination["next_bridge"]):
            self._set_pose(destination["mode"], destination["state"],
                           seconds=destination["seconds"], bridge=destination["next_bridge"])
            return
        self.mode = destination["mode"]
        self.pet.set_state(destination["state"])
        if destination["seconds"] is not None:
            self.mode_until = time.monotonic() + destination["seconds"]
        if self.mode == "walk":
            self.pet.play_transition("walk_start")
        elif self.mode == "idle":
            self.pet.play_transition("walk_stop")

    def advance_transition(self) -> bool:
        if not self.mode.startswith("transition:"):
            return True
        end_frame = self._transition_to.get("end_frame") if self._transition_to else None
        if self.pet.one_shot_finished() or (end_frame is not None and self.pet.frame_idx > end_frame):
            self._finish_transition()
            return not self.mode.startswith("transition:")
        return False

    def move_toward(self, target_x: int, dt: float) -> bool:
        arrived, new_vel, new_accum, step = calculate_walk_displacement(
            target_x, self.pet.x(), self._walk_accum, self._walk_velocity,
            self.WALK_SPEED, self.WALK_RAMP_SECONDS, dt)
        if arrived:
            self.pet.move(target_x, self.pet.y())
            self.reset_walk()
            return True
        facing_left = new_vel < 0
        if (self.pet.state == "walk" and abs(new_vel) > 0.01
                and facing_left != self.pet.facing_left and self.pet.sprites.has("turn_front")
                and self.pet.sprites.has("turn_bridge")):
            self.reset_walk()
            self._set_pose("walk", "walk", bridge="turn_front")
            self._transition_to.update(next_bridge="turn_bridge", facing_left=facing_left)
            return False
        self._walk_velocity = new_vel
        self._walk_accum = new_accum
        if abs(self._walk_velocity) > 0.01:
            self.pet.facing_left = self._walk_velocity < 0
        if step:
            self.pet.move(self.pet.x() + step, self.pet.y())
        return False

    def wander(self, *, reset_count: bool = True):
        if self.pet.reduced_motion:
            return
        self._interrupt_thinking()
        self._set_fully_asleep(False)
        if reset_count:
            self.idle_ticks = 0
        self.target_x = calculate_wander_target(self.pet.screen_bounds(), self.pet.width())
        if self.mode != "walk":
            self.reset_walk()
            self.pet.facing_left = self.target_x < self.pet.x()
        self._set_pose("walk", "walk", bridge="turn_bridge" if self.pet.state != "walk" else None)
        if self.mode == "walk":
            self.pet.play_transition("walk_start")

    def idle(self, seconds: float = 0.0, *, reset_count: bool = True):
        was_sleeping = self.sleeping
        wake_frame = self._wake_start_frame()
        was_walking = self.pet.state == "walk"
        self.reset_walk()
        self._interrupt_thinking()
        self._set_fully_asleep(False)
        if reset_count:
            self.idle_ticks = 0
        rest = seconds or (random.uniform(9.0, 16.0) if self.slow else random.uniform(2.5, 6.0))
        bridge = "turn_front" if was_walking else ("wake_bridge" if was_sleeping else None)
        self._set_pose("idle", "idle", seconds=rest, bridge=bridge, start_frame=wake_frame)
        if was_walking and self.mode == "idle":
            self.pet.play_transition("walk_stop")

    def sleep(self):
        source, source_frame = self.pet.state, self.pet.frame_idx
        self.reset_walk()
        self._interrupt_thinking()
        self._set_fully_asleep(False)
        self._auto_nap = False
        self.idle_ticks = 0
        bridge, start_frame, end_frame = resolve_sleep_bridge(source, source_frame)
        self._set_pose("sleep", "sleep", seconds=45.0,
                       bridge=bridge if source != "sleep" else None,
                       start_frame=start_frame, end_frame=end_frame)

    def deep_sleep(self, *, nap: bool = False):
        self._interrupt_thinking()
        self.reset_walk()
        self._set_fully_asleep(False)
        self._auto_nap = nap
        self.idle_ticks = 0
        self._deep_state = "liesleep"
        source = self.pet.state
        start_frame = resolve_deep_sleep_start_frame(source, self.pet.frame_idx)
        low, high = self.AUTO_NAP_SECONDS_SLOW if self.slow else self.AUTO_NAP_SECONDS
        self._set_pose("deepsleep", "liesleep",
                       seconds=random.uniform(low, high) if nap else None,
                       bridge="sleep_bridge" if source not in ("liesleep", "still") else None,
                       start_frame=start_frame)

    def still_sleep(self):
        self._interrupt_thinking()
        self.reset_walk()
        self._auto_nap = False
        self.mode = "deepsleep"
        self.idle_ticks = 0
        self._deep_state = "still"
        self.pet.set_state("still")
        self._set_fully_asleep(True)

    def dance(self, seconds: float = 2.4):
        if self.pet.reduced_motion:
            return
        self._interrupt_thinking()
        self.reset_walk()
        self._set_fully_asleep(False)
        self.mode = "dance"
        if self.pet.state == "dance":
            self.pet.set_state("idle")
        self.pet.set_state("dance")
        self.mode_until = time.monotonic() + seconds
        metadata = self.pet._animation_meta()
        cycle_ms = sum(metadata["durations"]) * self.pet.time_scale
        self._dance_cycle_target = self.pet._completed_cycles + max(1, math.ceil(seconds * 1000 / max(1.0, cycle_ms)))

    def play_one_shot(self, state: str, then: str = "idle"):
        if self.pet.reduced_motion or not self.pet.sprites.has(state):
            return
        self._interrupt_thinking()
        self.reset_walk()
        self._set_fully_asleep(False)
        self.mode = f"oneshot:{then}"
        if self.pet.state == state:
            self.pet.set_state("idle")
        self.pet.set_state(state)

    def can_play_extra_motion(self, state: str) -> bool:
        return (state in ("stretch", "bow") and self.pet.sprites.has(state)
                and not self.pet.reduced_motion and not self.sleeping
                and not self.onboarding_active and not self.pet._dragging
                and not self._fell and self.mode not in ("thinking", "drag", "fall"))

    def play_extra_motion(self, state: str) -> bool:
        if not self.can_play_extra_motion(state):
            return False
        if self.pet.state == state:
            self.pet.set_state("idle")
        self.play_one_shot(state)
        return True

    def run_action(self, name: str):
        table = {
            "wave": lambda: self.play_one_shot("wave"),
            "celebrate": lambda: self.play_one_shot("celebrate"),
            "jump": lambda: self.play_one_shot("jump"),
            "stretch": lambda: self.play_extra_motion("stretch"),
            "bow": lambda: self.play_extra_motion("bow"),
            "dance": self.dance,
            "sleep": self.sleep,
            "liesleep": self.deep_sleep,
            "still": self.still_sleep,
            "idle": self.idle,
            "wander": self.wander,
            "open_hub": lambda: self.hub_requested.emit(),
        }
        fn = table.get(name)
        if fn:
            fn()

    def _run_click_action(self, action_name: str) -> None:
        if action_name == "open_hub":
            self.hub_requested.emit()
        else:
            self.run_action(action_name)

    def _on_drag_started(self):
        if self.mode != "drag":
            self.reset_walk()
            if self.mode == "thinking":
                self._interrupt_thinking()
                self.mode = "idle"
                self.pet.set_state("idle")
            pending = self._transition_to
            self._pre_fall_mode = pending["mode"] if pending else self.mode
            self._pre_drag_state = pending["state"] if pending else self.pet.state
            self._transition_to = None
            self.mode = "drag"
            self.pet.set_state("drag")
        self._fell = True

    def _on_clicked(self):
        if self.onboarding_active:
            return
        self.idle_ticks = 0
        if self.mode == "drag" and self._fell:
            self.mode = self._pre_fall_mode
            if self.mode != "fall":
                self._fell = False
                self.pet.set_state(self._pre_drag_state)
        if self.sleeping:
            self._pre_fall_mode = "idle"
            wake_frame = self._wake_start_frame()
            self._interrupt_thinking()
            self.reset_walk()
            self._set_fully_asleep(False)
            self._set_pose("oneshot:idle", "wave", bridge="wake_bridge", start_frame=wake_frame)
        elif self.mode not in ("drag", "fall"):
            action = self.click_actions.get("click", self.click_actions.get("single_click", "wave"))
            self._run_click_action(action)

    def _on_double_clicked(self):
        if not self.onboarding_active:
            self.idle_ticks = 0
            action = self.click_actions.get("double_click", "sleep")
            self._run_click_action(action)

    def _finish_fall(self):
        self._fell = False
        if self.fully_asleep:
            self.still_sleep()
        elif self._pre_fall_mode == "sleep":
            nap = self._auto_nap
            self.sleep()
            self._auto_nap = nap
        elif self._pre_fall_mode == "deepsleep":
            self.mode = "deepsleep"
            self.pet.set_state(self._deep_state)
        else:
            self.idle(1.5)

    def _tick(self):
        now = time.monotonic()
        dt = max(0.0, min(now - self._last_tick_time, 0.05))
        self._last_tick_time = now
        pet = self.pet

        if pet._dragging:
            self._on_drag_started()
            return

        if pet.reduced_motion:
            if self._fell:
                pet.vx = pet.vy = 0.0
                bounds = pet.screen_bounds()
                pet.move(max(bounds.left(), min(pet.x(), bounds.right() - pet.width() + 1)),
                         max(bounds.top(), min(pet.y(), bounds.bottom() - pet.height() + 1)))
                self._finish_fall()
            elif not self.onboarding_active:
                if self.mode.startswith("transition:"):
                    self._finish_transition()
                if self.mode in ("walk", "dance") or self.mode.startswith("oneshot:"):
                    self.idle()
            return

        if self._fell:
            if self.mode != "fall":
                self.mode = "fall"
                pet.set_state("drag")
            if pet.physics_step(dt):
                self._finish_fall()
            return

        if self.onboarding_active:
            return

        if self.mode.startswith("transition:"):
            self.advance_transition()
        elif self.mode == "walk":
            if self.move_toward(self.target_x, dt):
                if random.random() < 0.2:
                    self.play_one_shot("jump")
                else:
                    self.idle(reset_count=False)
        elif self.mode == "idle" and now >= self.mode_until:
            self.idle_ticks += 1
            if self.idle_ticks >= 4 and random.random() < 0.45:
                self.idle_ticks = 0
                self.sleep()
                self._auto_nap = True
            elif random.random() < 0.18:
                self.play_one_shot("jump")
            else:
                gestures = [s for s in ("stretch", "bow") if self.can_play_extra_motion(s)]
                if gestures and random.random() < 0.16:
                    self.play_extra_motion(random.choice(gestures))
                else:
                    self.wander(reset_count=False)
        elif self.mode == "dance" and pet._completed_cycles >= self._dance_cycle_target:
            self.idle()
        elif self.mode == "sleep" and now >= self.mode_until:
            self.deep_sleep(nap=self._auto_nap)
        elif self.mode == "deepsleep" and self._auto_nap and now >= self.mode_until:
            self.wake()
        elif self.mode.startswith("oneshot:") and pet.one_shot_finished():
            nxt = self.mode.split(":", 1)[1]
            if nxt == "idle":
                self.idle(reset_count=False)
            elif nxt == "wander":
                self.wander(reset_count=False)
            else:
                self.sleep()
