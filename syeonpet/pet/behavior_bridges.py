"""Behavior bridge, pose transition and displacement mathematics helpers."""
from __future__ import annotations

import math
import random
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from behavior import BehaviorController


def resolve_wake_start_frame(state: str, frame_idx: int, thinking_restore: dict | None) -> int:
    source, frame = state, frame_idx
    if thinking_restore:
        source = thinking_restore.get("state", state)
        frame = thinking_restore.get("frame_idx", frame_idx)
    if source == "sleep":
        return 3
    if source == "sleep_bridge":
        return max(0, 5 - frame)
    if source == "wake_bridge":
        return frame
    return 0


def resolve_sleep_bridge(state: str, frame_idx: int) -> tuple[str, int, int]:
    rising = state in ("liesleep", "still") or (
        state == "wake_bridge" and frame_idx <= 3) or (
        state == "sleep_bridge" and frame_idx > 2)
    bridge = "wake_bridge" if rising else "sleep_bridge"
    start_frame = 0
    if state in ("sleep_bridge", "wake_bridge"):
        start_frame = frame_idx if state == bridge else 5 - frame_idx
    end_frame = 3 if bridge == "wake_bridge" else 2
    return bridge, start_frame, end_frame


def resolve_deep_sleep_start_frame(state: str, frame_idx: int) -> int:
    if state == "sleep":
        return 3
    if state == "wake_bridge":
        return 5 - frame_idx
    if state == "sleep_bridge":
        return frame_idx
    return 0


def calculate_wander_target(bounds, pet_width: int) -> int:
    left = bounds.left() + 10
    right = bounds.right() - pet_width - 10
    if right < left:
        left = bounds.left()
        right = max(left, bounds.right() - pet_width + 1)
    return random.randint(left, right)


def calculate_walk_displacement(
    target_x: int,
    pet_x: int,
    walk_accum: float,
    walk_velocity: float,
    walk_speed: float,
    walk_ramp_seconds: float,
    dt: float,
) -> tuple[bool, float, float, int]:
    if not math.isfinite(dt) or dt <= 0:
        return (target_x == pet_x and not walk_accum), walk_velocity, walk_accum, 0
    dt = min(dt, 0.05)
    distance = target_x - pet_x - walk_accum
    if abs(distance) < 0.01:
        return True, 0.0, 0.0, 0
    if not math.isfinite(walk_speed) or walk_speed <= 0:
        return False, 0.0, 0.0, 0
    acceleration = walk_speed / walk_ramp_seconds
    desired = math.copysign(min(walk_speed, math.sqrt(1.5 * acceleration * abs(distance))), distance)
    change = max(-acceleration * dt, min(desired - walk_velocity, acceleration * dt))
    new_velocity = walk_velocity + change
    displacement = (walk_velocity + new_velocity) * 0.5 * dt
    if displacement * distance > 0 and abs(displacement) >= abs(distance):
        return True, 0.0, 0.0, 0
    new_accum = walk_accum + displacement
    step = math.trunc(new_accum)
    new_accum -= step
    return False, new_velocity, new_accum, step


def restore_thinking_snapshot(controller: BehaviorController, saved: dict) -> None:
    pet = controller.pet
    if not pet.sprites.has(saved["state"]):
        destination = saved.get("transition_to")
        if destination and pet.sprites.has(destination["state"]):
            controller._transition_to = destination
            controller.idle_ticks = saved["idle_ticks"]
            controller.target_x = saved["target_x"]
            controller.reset_walk()
            controller._finish_transition()
            return
        controller.idle(reset_count=False)
        return
    controller.mode = saved["mode"]
    controller.mode_until = time.monotonic() + saved["remaining"]
    controller.idle_ticks = saved["idle_ticks"]
    controller.target_x = saved["target_x"]
    controller._walk_accum = saved["walk_accum"]
    controller._walk_velocity = saved["walk_velocity"]
    controller._dance_cycle_target = saved["dance_cycle_target"]
    controller._transition_to = saved["transition_to"]
    pet.set_state(saved["state"])
    pet._completed_cycles = saved["completed_cycles"]
    metadata = pet._animation_meta()
    pet.frame_idx = min(saved["frame_idx"], len(metadata["frames"]) - 1)
    pet._frame_elapsed = min(saved["frame_elapsed"], metadata["durations"][pet.frame_idx] * pet.time_scale)
    pet._one_shot_done = saved["one_shot_done"]
    pet._update_mask()
    pet.update()
