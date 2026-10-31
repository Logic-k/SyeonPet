"""Thinking state does not override the pet's other behaviours."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

from behavior import BehaviorController
from pet import ASSETS, PetWidget, SpriteBank


class ThinkingBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.sprites = SpriteBank(ASSETS)

    def setUp(self):
        self.pet = PetWidget(self.sprites)
        self.behavior = BehaviorController(self.pet)
        self.pet.tick.stop()
        self.behavior.timer.stop()
        self.sprites.states["thinking"] = self.sprites.states["idle"].copy()

    def tearDown(self):
        self.sprites.states.pop("thinking", None)
        self.pet.close()
        self.app.processEvents()

    def finish_now(self):
        self.behavior._thinking_started -= 1.0
        self.behavior.end_thinking()
        self.finish_bridge()

    def finish_bridge(self):
        if self.behavior.mode.startswith("transition:"):
            self.assertIn(self.pet.state, ("sleep_bridge", "wake_bridge", "turn_bridge", "turn_front"))
            self.pet._advance_frame(sum(self.pet._animation_meta()["durations"]) * self.pet.time_scale)
            self.behavior._tick()
            self.assertFalse(self.behavior.mode.startswith("transition:"))

    def test_loop_uses_thinking_sprite_and_resumes_walk_without_position_jump(self):
        self.behavior.wander()
        start, target = self.pet.pos(), self.behavior.target_x
        self.behavior.begin_thinking()
        self.assertEqual((self.behavior.mode, self.pet.state), ("thinking", "thinking"))
        self.behavior._tick()
        self.assertEqual(self.pet.pos(), start)
        self.finish_now()
        self.assertEqual((self.behavior.mode, self.pet.state), ("walk", "walk"))
        self.assertEqual(self.behavior.target_x, target)
        self.assertEqual(self.pet.pos(), start)

    def test_missing_thinking_sprite_uses_existing_idle_and_restores_previous(self):
        self.sprites.states.pop("thinking")
        self.behavior.wander()
        self.behavior.begin_thinking()
        self.assertEqual((self.behavior.mode, self.pet.state), ("thinking", "idle"))
        self.finish_now()
        self.assertEqual((self.behavior.mode, self.pet.state), ("walk", "walk"))

    def test_repeated_begin_and_end_are_idempotent(self):
        self.behavior.wander()
        self.behavior.begin_thinking()
        self.behavior.begin_thinking()
        self.finish_now()
        self.finish_now()
        self.assertEqual((self.behavior.mode, self.pet.state), ("walk", "walk"))

    def test_user_sleep_or_other_selected_motion_wins_over_completion(self):
        for action, expected in ((self.behavior.still_sleep, ("deepsleep", "still")),
                                 (self.behavior.sleep, ("sleep", "sleep")),
                                 (self.behavior.dance, ("dance", "dance"))):
            with self.subTest(action=action.__name__):
                self.behavior.idle()
                self.behavior.begin_thinking()
                action()
                self.behavior.end_thinking()
                self.finish_bridge()
                self.assertEqual((self.behavior.mode, self.pet.state), expected)

    def test_request_never_wakes_completely_sleeping_pet_or_overrides_onboarding(self):
        self.behavior.still_sleep()
        original = (self.behavior.mode, self.pet.state, self.behavior.fully_asleep)
        self.behavior.begin_thinking()
        self.behavior.end_thinking()
        self.assertEqual((self.behavior.mode, self.pet.state, self.behavior.fully_asleep), original)
        self.behavior.idle()
        self.finish_bridge()
        self.behavior.onboarding_active = True
        self.behavior.begin_thinking()
        self.behavior.end_thinking()
        self.assertEqual(self.behavior.mode, "idle")

    def test_lighter_sleep_shows_thinking_then_restores_sleep(self):
        for action, expected in ((self.behavior.sleep, ("sleep", "sleep")),
                                 (self.behavior.deep_sleep, ("deepsleep", "liesleep"))):
            with self.subTest(action=action.__name__):
                action()
                self.behavior.begin_thinking()
                self.assertTrue(self.behavior.sleeping)
                self.assertEqual((self.behavior.mode, self.pet.state), ("thinking", "thinking"))
                self.finish_now()
                self.assertEqual((self.behavior.mode, self.pet.state), expected)
                self.assertTrue(self.behavior.sleeping)
                self.assertFalse(self.behavior.fully_asleep)

    def test_sleep_remaining_time_is_frozen_while_thinking(self):
        with patch("behavior.time.monotonic", return_value=10.0):
            self.behavior.sleep()
            self.finish_bridge()
            self.behavior.begin_thinking()
        with patch("behavior.time.monotonic", return_value=110.0):
            self.behavior.end_thinking()
        self.assertEqual(self.behavior.mode, "sleep")
        self.assertEqual(self.behavior.mode_until, 155.0)

    def test_explicit_wake_during_thinking_from_light_sleep_wins(self):
        for action in (self.behavior.sleep, self.behavior.deep_sleep):
            with self.subTest(action=action.__name__):
                action()
                self.behavior.begin_thinking()
                self.assertTrue(self.behavior.sleeping)
                self.behavior.wake()
                self.assertFalse(self.behavior.sleeping)
                self.finish_bridge()
                self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))
                self.assertFalse(self.behavior.sleeping)
                self.behavior.end_thinking()
                self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))

    def test_tray_sleep_toggle_wakes_interrupted_sleep_and_cancels_tail(self):
        action = QAction("재우기 / 깨우기")
        action.triggered.connect(self.behavior.toggle_sleep)
        for sleep in (self.behavior.sleep, self.behavior.deep_sleep):
            with self.subTest(sleep=sleep.__name__):
                sleep()
                self.behavior.begin_thinking()
                self.behavior.end_thinking()
                self.assertTrue(self.behavior._thinking_finish_pending)
                action.trigger()
                self.assertFalse(self.behavior.sleeping)
                self.finish_bridge()
                self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))
                self.assertFalse(self.behavior.sleeping)
                self.behavior._finish_thinking()
                self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))

    def test_tray_wake_during_fall_does_not_restore_old_sleep(self):
        action = QAction("재우기 / 깨우기")
        action.triggered.connect(self.behavior.toggle_sleep)
        self.behavior.deep_sleep()
        self.pet._dragging = True
        self.behavior._on_drag_started()
        self.pet._dragging = False
        with patch.object(self.pet, "physics_step", side_effect=(False, True)):
            self.behavior._tick()
            self.assertEqual(self.behavior.mode, "fall")
            action.trigger()
            self.assertFalse(self.behavior.sleeping)
            self.behavior._tick()
        self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))

    def test_drag_during_thinking_from_sleep_does_not_restore_sleep(self):
        self.behavior.sleep()
        self.behavior.begin_thinking()
        self.pet._dragging = True
        self.behavior._on_drag_started()
        self.behavior.end_thinking()
        self.pet._dragging = False
        with patch.object(self.pet, "physics_step", return_value=True):
            self.behavior._tick()
        self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))
        self.assertFalse(self.behavior.sleeping)

    def test_drag_during_request_wins_and_does_not_resume_stale_thinking(self):
        self.behavior.wander()
        self.behavior.begin_thinking()
        self.pet._dragging = True
        self.behavior._on_drag_started()
        self.behavior.end_thinking()
        self.assertEqual(self.behavior.mode, "drag")
        self.pet._dragging = False
        with patch.object(self.pet, "physics_step", return_value=True):
            self.behavior._tick()
        self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))

    def test_previous_one_shot_frame_is_restored_after_fast_response(self):
        self.behavior.play_one_shot("wave")
        self.pet.frame_idx = 2
        self.pet._frame_elapsed = 25.0
        self.behavior.begin_thinking()
        self.finish_now()
        self.assertEqual((self.behavior.mode, self.pet.state), ("oneshot:idle", "wave"))
        self.assertEqual((self.pet.frame_idx, self.pet._frame_elapsed), (2, 25.0))

    def test_remaining_idle_time_is_frozen_during_request(self):
        with patch("behavior.time.monotonic", return_value=10.0):
            self.behavior.idle(3.0)
            self.behavior.begin_thinking()
        with patch("behavior.time.monotonic", return_value=110.0):
            self.behavior.end_thinking()
        self.assertEqual(self.behavior.mode_until, 113.0)

    def test_fast_response_leaves_visible_tail_then_restores(self):
        with patch("behavior.time.monotonic", return_value=10.0):
            self.behavior.wander()
            self.behavior.begin_thinking()
        with patch("behavior.time.monotonic", return_value=10.1):
            self.behavior.end_thinking()
        self.assertEqual(self.behavior.mode, "thinking")
        self.assertTrue(self.behavior._thinking_finish_pending)
        self.behavior._finish_thinking()
        self.finish_bridge()
        self.assertEqual(self.behavior.mode, "walk")

    def test_new_request_cancels_visual_tail_without_overwriting_snapshot(self):
        self.behavior.wander()
        self.behavior.begin_thinking()
        self.behavior.end_thinking()
        self.assertTrue(self.behavior._thinking_finish_pending)
        old_generation = self.behavior._thinking_generation
        self.behavior.begin_thinking()
        self.assertFalse(self.behavior._thinking_finish_pending)
        # A queued callback for the previous request cannot end the new one.
        self.behavior._finish_thinking(old_generation)
        self.assertEqual(self.behavior.mode, "thinking")
        self.finish_now()
        self.assertEqual((self.behavior.mode, self.pet.state), ("walk", "walk"))

    def test_manual_sleep_during_visual_tail_cancels_timer(self):
        self.behavior.begin_thinking()
        self.behavior.end_thinking()
        old_generation = self.behavior._thinking_generation
        self.assertTrue(self.behavior._thinking_finish_pending)
        self.behavior.still_sleep()
        self.assertFalse(self.behavior._thinking_finish_pending)
        self.behavior._finish_thinking(old_generation)  # stale timeout is harmless
        self.assertEqual((self.behavior.mode, self.pet.state), ("deepsleep", "still"))


if __name__ == "__main__":
    unittest.main()
