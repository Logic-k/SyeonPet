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
    WALK_RAMP_SECONDS = 0.25
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
        self._walk_velocity = 0.0
        self._dance_cycle_target = 0
        self._transition_to: dict | None = None
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
        if not self.pet.sprites.has(saved["state"]):
            # A pack can be changed while a request owns the temporary pose.
            # A missing connecting row still has a valid public destination;
            # in particular, changing clothing must not wake a sleeping pet.
            destination = saved.get("transition_to")
            if destination and self.pet.sprites.has(destination["state"]):
                self._transition_to = destination
                self.idle_ticks = saved["idle_ticks"]
                self.target_x = saved["target_x"]
                self.reset_walk()
                self._finish_transition()
                return
            # Do not restore an optional one-shot or its cursor without its row.
            self.idle(reset_count=False)
            return
        self.mode = saved["mode"]
        self.mode_until = time.monotonic() + saved["remaining"]
        self.idle_ticks = saved["idle_ticks"]
        self.target_x = saved["target_x"]
        self._walk_accum = saved["walk_accum"]
        self._walk_velocity = saved["walk_velocity"]
        self._dance_cycle_target = saved["dance_cycle_target"]
        self._transition_to = saved["transition_to"]
        self.pet.set_state(saved["state"])
        self.pet._completed_cycles = saved["completed_cycles"]
        metadata = self.pet._animation_meta()
        self.pet.frame_idx = min(saved["frame_idx"], len(metadata["frames"]) - 1)
        self.pet._frame_elapsed = min(saved["frame_elapsed"],
                                     metadata["durations"][self.pet.frame_idx] * self.pet.time_scale)
        self.pet._one_shot_done = saved["one_shot_done"]
        self.pet._update_mask()
        self.pet.update()

    def _interrupt_thinking(self):
        """Forget the snapshot when the user explicitly chooses a new action."""
        self._thinking_generation += 1
        self._thinking_finish_pending = False
        self._thinking_restore = None
        self._transition_to = None

    @property
    def fully_asleep(self) -> bool:
        """The explicit motionless sleep mode, including while being dragged."""
        return self._fully_asleep

    @property
    def sleeping(self) -> bool:
        """Whether the current or temporarily interrupted state is sleep."""
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

    def reset_walk(self):
        """Discard movement momentum when another interaction takes over."""
        self._walk_accum = 0.0
        self._walk_velocity = 0.0

    def _wake_start_frame(self) -> int:
        source, frame = self.pet.state, self.pet.frame_idx
        if self.mode == "thinking" and self._thinking_restore:
            source = self._thinking_restore["state"]
            frame = self._thinking_restore["frame_idx"]
        if source == "sleep":
            return 3
        if source == "sleep_bridge":
            return max(0, 5 - frame)
        if source == "wake_bridge":
            return frame
        return 0

    def _set_pose(self, mode: str, state: str, *, seconds: float | None = None,
                  bridge: str | None = None, start_frame: int = 0,
                  end_frame: int | None = None):
        """Play an optional connecting row before entering a public action.

        The destination is data so thinking can pause it, while a new user
        action or a drag can discard it without a delayed callback firing.
        """
        self._transition_to = None
        if (bridge and self.pet.sprites.has(bridge) and not self.pet.reduced_motion
                and not self.pet._dragging and not self._fell):
            self._transition_to = {"mode": mode, "state": state, "seconds": seconds,
                                   "end_frame": end_frame}
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
        """Finish a bridge at its last intended pose; also used by the tour."""
        if not self.mode.startswith("transition:"):
            return True
        end_frame = self._transition_to.get("end_frame") if self._transition_to else None
        if (self.pet.one_shot_finished()
                or (end_frame is not None and self.pet.frame_idx > end_frame)):
            self._finish_transition()
            return not self.mode.startswith("transition:")
        return False

    def move_toward(self, target_x: int, dt: float) -> bool:
        """Move with a short acceleration/braking phase; retain subpixels.

        Both wandering and the tour use the same elapsed-time movement. A
        changed target first brakes the old direction before the pet turns.
        """
        if not math.isfinite(dt) or dt <= 0:
            return target_x == self.pet.x() and not self._walk_accum
        dt = min(dt, 0.05)
        distance = target_x - self.pet.x() - self._walk_accum
        if abs(distance) < 0.01:
            self.pet.move(target_x, self.pet.y())
            self.reset_walk()
            return True
        if not math.isfinite(self.WALK_SPEED) or self.WALK_SPEED <= 0:
            self.reset_walk()
            return False
        acceleration = self.WALK_SPEED / self.WALK_RAMP_SECONDS
        # Start braking before the ideal stopping distance to allow for the
        # next discrete timer step without snapping the final stride short.
        desired = math.copysign(min(self.WALK_SPEED,
                                   math.sqrt(1.5 * acceleration * abs(distance))), distance)
        previous = self._walk_velocity
        change = max(-acceleration * dt, min(desired - previous, acceleration * dt))
        self._walk_velocity += change
        facing_left = self._walk_velocity < 0
        if (self.pet.state == "walk" and abs(self._walk_velocity) > 0.01
                and facing_left != self.pet.facing_left and self.pet.sprites.has("turn_front")
                and self.pet.sprites.has("turn_bridge")):
            # Brake to a standstill before turning through the front pose.
            # Mirroring only that front pose avoids flipping a side-view
            # stride in the middle of a step.
            self.reset_walk()
            self._set_pose("walk", "walk", bridge="turn_front")
            self._transition_to.update(next_bridge="turn_bridge", facing_left=facing_left)
            return False
        displacement = (previous + self._walk_velocity) * 0.5 * dt
        if displacement * distance > 0 and abs(displacement) >= abs(distance):
            self.pet.move(target_x, self.pet.y())
            self.reset_walk()
            return True
        self._walk_accum += displacement
        step = math.trunc(self._walk_accum)
        self._walk_accum -= step
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
        bounds = self.pet.screen_bounds()
        left = bounds.left() + 10
        right = bounds.right() - self.pet.width() - 10
        if right < left:
            # A narrow work area cannot fit both margins (or the whole pet).
            left = bounds.left()
            right = max(left, bounds.right() - self.pet.width() + 1)
        self.target_x = random.randint(left, right)
        if self.mode != "walk":
            self.reset_walk()
        if self.mode != "walk":
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
        rest = seconds or (random.uniform(9.0, 16.0) if self.slow
                           else random.uniform(2.5, 6.0))
        bridge = "turn_front" if was_walking else (
            "wake_bridge" if was_sleeping else None)
        self._set_pose("idle", "idle", seconds=rest, bridge=bridge,
                       start_frame=wake_frame)
        if was_walking and self.mode == "idle":
            self.pet.play_transition("walk_stop")

    def sleep(self):
        source = self.pet.state
        source_frame = self.pet.frame_idx
        self.reset_walk()
        self._interrupt_thinking()
        self._set_fully_asleep(False)
        self.idle_ticks = 0
        rising_to_sit = source in ("liesleep", "still") or (
            source == "wake_bridge" and source_frame <= 3) or (
            source == "sleep_bridge" and source_frame > 2)
        bridge = "wake_bridge" if rising_to_sit else "sleep_bridge"
        start_frame = 0
        if source in ("sleep_bridge", "wake_bridge"):
            start_frame = source_frame if source == bridge else 5 - source_frame
        self._set_pose("sleep", "sleep", seconds=45.0,
                       bridge=bridge if source != "sleep" else None,
                       start_frame=start_frame,
                       end_frame=3 if bridge == "wake_bridge" else 2)

    def deep_sleep(self):
        """Lying-down sleep — stays put, no wander, until woken."""
        self._interrupt_thinking()
        self.reset_walk()
        self._set_fully_asleep(False)
        self.idle_ticks = 0
        self._deep_state = "liesleep"
        source = self.pet.state
        start_frame = (3 if source == "sleep" else
                       5 - self.pet.frame_idx if source == "wake_bridge" else
                       self.pet.frame_idx if source == "sleep_bridge" else 0)
        self._set_pose("deepsleep", "liesleep",
                       bridge="sleep_bridge" if source not in ("liesleep", "still") else None,
                       start_frame=start_frame)

    def still_sleep(self):
        """Completely motionless flop — nothing moves until woken."""
        self._interrupt_thinking()
        self.reset_walk()
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
        self._dance_cycle_target = self.pet._completed_cycles + max(
            1, math.ceil(seconds * 1000 / max(1.0, cycle_ms)))

    def play_one_shot(self, state: str, then: str = "idle"):
        """wave / celebrate; returns to `then` when the strip finishes."""
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
        """Optional gestures never interrupt work, sleep, a tour or a drag."""
        return (state in ("stretch", "bow") and self.pet.sprites.has(state)
                and not self.pet.reduced_motion and not self.sleeping
                and not self.onboarding_active and not self.pet._dragging
                and not self._fell and self.mode not in ("thinking", "drag", "fall"))

    def play_extra_motion(self, state: str) -> bool:
        if not self.can_play_extra_motion(state):
            return False
        # Selecting the same gesture again starts a fresh one-shot.
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
        }
        fn = table.get(name)
        if fn:
            fn()

    # ---------- events ----------

    def _on_drag_started(self):
        # This can arrive before the next timer tick, including a drag whose
        # press, move, and release all happen between two ticks.
        if self.mode != "drag":
            self.reset_walk()
            if self.mode == "thinking":
                # A direct gesture takes precedence over an in-flight request.
                # Do not resume a stale thinking state after landing.
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
        # A normal press may last longer than one timer tick.  _tick then
        # temporarily sets drag even when the pointer never actually moved;
        # clicked means mouseReleaseEvent has classified it as a click.
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
            self._set_pose("oneshot:idle", "wave", bridge="wake_bridge",
                           start_frame=wake_frame)
        elif self.mode not in ("drag", "fall"):
            self.run_action(self.click_actions.get("click", "wave"))

    def _on_double_clicked(self):
        if not self.onboarding_active:
            self.idle_ticks = 0
            self.run_action(self.click_actions.get("double_click", "sleep"))

    # ---------- tick ----------

    def _finish_fall(self):
        self._fell = False
        if self.fully_asleep:
            self.still_sleep()
        elif self._pre_fall_mode == "sleep":
            self.sleep()
        elif self._pre_fall_mode == "deepsleep":
            self.mode = "deepsleep"
            self.pet.set_state(self._deep_state)
        else:
            self.idle(1.5)

    def _tick(self):
        now = time.monotonic()
        # A suspended window or system resume must not teleport the pet.
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
            elif random.random() < 0.18:
                self.play_one_shot("jump")
            else:
                gestures = [state for state in ("stretch", "bow")
                            if self.can_play_extra_motion(state)]
                if gestures and random.random() < 0.16:
                    self.play_extra_motion(random.choice(gestures))
                else:
                    self.wander(reset_count=False)

        elif self.mode == "dance" and pet._completed_cycles >= self._dance_cycle_target:
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
