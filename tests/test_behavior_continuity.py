"""Elapsed-time movement and action-boundary regression simulations."""
import os
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings, QRect
from PySide6.QtWidgets import QApplication

from behavior import BehaviorController
from bubble import SpeechBubble
from onboarding import OnboardingTour
from pet import ASSETS, PetWidget, SpriteBank
from wardrobe_packs import import_sprite_pack


class BehaviorContinuityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.sprites = SpriteBank(ASSETS)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        settings = QSettings(str(Path(self.temporary.name) / "wardrobe.ini"),
                             QSettings.Format.IniFormat)
        bank = copy.copy(self.sprites)
        bank.states = dict(self.sprites.states)
        self.pet = PetWidget(bank, display_size=64, wardrobe_settings=settings)
        self.pet.tick.stop()
        self.behavior = BehaviorController(self.pet)
        self.behavior.timer.stop()
        self.pet.move(100, 200)
        self.tours = []

    def tearDown(self):
        for tour in self.tours:
            tour._tick.stop()
        self.pet.close()
        self.app.processEvents()
        self.temporary.cleanup()

    def tour(self):
        bubble = SpeechBubble(self.pet)
        self.pet.bubble_widget = bubble
        tour = OnboardingTour(self.pet, self.behavior, bubble,
                              [{"text": "hello", "anchor": "right"}])
        self.tours.append(tour)
        return tour

    def install_bridges(self):
        # Stable fixture exercises the same loader contract even when an
        # imported character pack does not provide the optional source rows.
        for state in ("sleep_bridge", "wake_bridge", "turn_bridge", "turn_front"):
            self.pet.sprites.states[state] = {
                "frames": self.pet.sprites.states["idle"]["frames"][:1] * 6,
                "durations": [100] * 6, "loop": False}

    def finish_bridge(self):
        self.assertTrue(self.behavior.mode.startswith("transition:"))
        self.pet._advance_frame(sum(self.pet._animation_meta()["durations"]))
        self.behavior._tick()

    def test_sleep_uses_sitting_then_lying_sections_and_reports_sleep_immediately(self):
        self.install_bridges()
        self.behavior.sleep()
        self.assertTrue(self.behavior.sleeping)
        self.assertEqual(self.pet.state, "sleep_bridge")
        self.assertEqual(self.pet.frame_idx, 0)
        self.pet._advance_frame(300)
        self.behavior._tick()
        self.assertEqual((self.behavior.mode, self.pet.state), ("sleep", "sleep"))
        self.behavior.mode_until = 0
        self.behavior._tick()
        self.assertEqual((self.pet.state, self.pet.frame_idx), ("sleep_bridge", 3))
        self.finish_bridge()
        self.assertEqual((self.behavior.mode, self.pet.state), ("deepsleep", "liesleep"))

    def test_wake_click_rises_before_wave_and_a_new_action_cancels_the_queue(self):
        self.install_bridges()
        self.behavior.still_sleep()
        self.behavior._on_clicked()
        self.assertFalse(self.behavior.fully_asleep)
        self.assertFalse(self.behavior.sleeping)
        self.assertEqual(self.pet.state, "wake_bridge")
        self.finish_bridge()
        self.assertEqual((self.behavior.mode, self.pet.state), ("oneshot:idle", "wave"))
        self.behavior.still_sleep()
        self.behavior.wake()
        self.behavior.play_one_shot("celebrate")
        self.assertIsNone(self.behavior._transition_to)
        self.pet._advance_frame(sum(self.pet._animation_meta()["durations"]))
        self.behavior._tick()
        self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))

    def test_still_and_reduced_motion_remain_immediate_with_bridges_installed(self):
        self.install_bridges()
        self.behavior.deep_sleep()
        self.behavior.still_sleep()
        self.assertEqual(self.pet.state, "still")
        self.assertTrue(self.behavior.fully_asleep)
        self.assertIsNone(self.behavior._transition_to)
        self.pet.set_reduced_motion(True)
        self.behavior.wake()
        self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))
        self.behavior.deep_sleep()
        self.assertEqual((self.behavior.mode, self.pet.state), ("deepsleep", "liesleep"))

    def test_turn_rows_surround_walking_without_moving_during_the_turn(self):
        self.install_bridges()
        start = self.pet.pos()
        with patch("behavior.random.randint", return_value=self.pet.x() + 80):
            self.behavior.wander()
        self.assertEqual(self.pet.state, "turn_bridge")
        self.behavior._tick()
        self.assertEqual(self.pet.pos(), start)
        self.finish_bridge()
        self.assertEqual((self.behavior.mode, self.pet.state), ("walk", "walk"))
        self.behavior.target_x = self.pet.x()
        with patch("behavior.random.random", return_value=0.8):
            self.behavior._tick()
        self.assertEqual(self.pet.state, "turn_front")
        self.finish_bridge()
        self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))

    def test_drag_interrupting_sleep_bridge_restores_its_sleep_destination(self):
        self.install_bridges()
        self.behavior.deep_sleep()
        self.pet._dragging = True
        self.behavior._tick()
        self.assertIsNone(self.behavior._transition_to)
        self.assertTrue(self.behavior.sleeping)
        self.pet._dragging = False
        with patch.object(self.pet, "physics_step", return_value=True):
            self.behavior._tick()
        self.assertEqual((self.behavior.mode, self.pet.state), ("deepsleep", "liesleep"))

    def test_thinking_resumes_a_bridge_and_its_destination(self):
        self.install_bridges()
        self.behavior.deep_sleep()
        self.pet._advance_frame(150)
        self.behavior.begin_thinking()
        self.assertTrue(self.behavior.sleeping)
        self.behavior._finish_thinking()
        self.assertEqual((self.pet.state, self.pet.frame_idx, self.pet._frame_elapsed),
                         ("sleep_bridge", 1, 50))
        self.finish_bridge()
        self.assertEqual(self.pet.state, "liesleep")

    def test_sleep_toggle_mid_bridge_reverses_from_the_current_pose(self):
        self.install_bridges()
        self.behavior.deep_sleep()
        self.pet._advance_frame(150)
        self.behavior.toggle_sleep()
        self.assertEqual((self.pet.state, self.pet.frame_idx), ("wake_bridge", 4))
        self.behavior.toggle_sleep()
        self.assertEqual((self.pet.state, self.pet.frame_idx), ("sleep_bridge", 1))
        self.finish_bridge()
        self.assertEqual(self.pet.state, "sleep")

    def test_direction_change_turns_through_front_before_mirroring(self):
        self.install_bridges()
        self.pet.set_state("walk")
        self.behavior.mode = "walk"
        for _ in range(30):
            self.behavior.move_toward(500, .016)
        for _ in range(30):
            self.behavior.move_toward(0, .016)
            if self.behavior.mode.startswith("transition:"):
                break
        self.assertEqual(self.pet.state, "turn_front")
        self.assertFalse(self.pet.facing_left)
        before = self.pet.pos()
        self.finish_bridge()
        self.assertTrue(self.pet.facing_left)
        self.assertEqual(self.pet.state, "turn_bridge")
        self.assertEqual(self.pet.pos(), before)
        self.finish_bridge()
        self.assertEqual(self.pet.state, "walk")
        self.assertEqual(self.pet.pos(), before)

    def test_reduction_mid_bridge_finishes_sleep_but_cancels_decorative_walk(self):
        self.install_bridges()
        for action, expected in ((self.behavior.deep_sleep, ("deepsleep", "liesleep")),
                                 (self.behavior.wander, ("idle", "idle"))):
            with self.subTest(action=action.__name__):
                self.pet.set_reduced_motion(False)
                self.pet.tick.stop()
                action()
                self.pet._advance_frame(150)
                self.pet.set_reduced_motion(True)
                self.behavior._tick()
                self.assertEqual((self.behavior.mode, self.pet.state), expected)
                self.assertIsNone(self.behavior._transition_to)

    def test_pack_without_bridge_finishes_pending_action_and_thinking_falls_back(self):
        self.install_bridges()
        packs = Path(self.temporary.name) / "packs"
        imported = import_sprite_pack(ASSETS, packs)
        with patch("pet.pack_root", return_value=packs):
            self.behavior.deep_sleep()
            self.pet._advance_frame(150)
            self.pet.set_wardrobe_pack(imported.name)
            self.assertEqual(self.pet.state, "idle")
            self.behavior._tick()
            self.assertEqual((self.behavior.mode, self.pet.state), ("deepsleep", "liesleep"))
            self.pet.set_wardrobe_pack("original")
            self.behavior.wake()
            self.behavior.begin_thinking()
            self.pet.set_wardrobe_pack(imported.name)
            self.behavior._finish_thinking()
            self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))
            self.assertIsNone(self.behavior._transition_to)
            self.pet.set_wardrobe_pack("original")
            self.behavior.deep_sleep()
            self.pet._advance_frame(150)
            self.behavior.begin_thinking()
            self.pet.set_wardrobe_pack(imported.name)
            self.behavior._finish_thinking()
            self.assertTrue(self.behavior.sleeping)
            self.assertEqual((self.behavior.mode, self.pet.state), ("deepsleep", "liesleep"))

    def walk_trace(self, cadence, seconds=2.0, target=900):
        self.pet.move(100, 200)
        self.behavior.reset_walk()
        elapsed = 0.0
        trace = []
        while elapsed < seconds - 1e-8:
            dt = min(cadence, seconds - elapsed)
            arrived = self.behavior.move_toward(target, dt)
            elapsed += dt
            trace.append((elapsed, self.pet.x(), self.behavior._walk_velocity))
            if arrived:
                break
        return trace

    def test_walk_ramps_up_and_is_consistent_at_16_and_30_ms(self):
        for slow in (False, True):
            with self.subTest(slow=slow):
                self.behavior.set_slow(slow)
                a = self.walk_trace(.016)
                b = self.walk_trace(.030)
                self.assertLess(abs(a[0][2]), self.behavior.WALK_SPEED / 4)
                self.assertAlmostEqual(a[-1][2], self.behavior.WALK_SPEED)
                self.assertLessEqual(abs(a[-1][1] - b[-1][1]), 1)
                self.assertGreater(a[-1][1] - a[0][1], self.behavior.WALK_SPEED)

    def test_walk_brakes_without_overshoot_and_reaches_exact_target(self):
        trace = self.walk_trace(.016, seconds=8, target=320)
        self.assertEqual(trace[-1][1:], (320, 0.0))
        self.assertTrue(all(100 <= row[1] <= 320 for row in trace))
        peak = max(row[2] for row in trace)
        self.assertLess(trace[-2][2], peak / 3)
        self.assertEqual(self.behavior._walk_accum, 0)

    def test_changed_target_brakes_before_turning(self):
        self.walk_trace(.016, seconds=.5)
        previous = self.pet.x()
        self.assertFalse(self.pet.facing_left)
        self.behavior.move_toward(50, .016)
        self.assertGreaterEqual(self.pet.x(), previous)
        self.assertFalse(self.pet.facing_left)
        for _ in range(30):
            self.behavior.move_toward(50, .016)
        self.assertTrue(self.pet.facing_left)
        self.assertLess(self.behavior._walk_velocity, 0)

    def test_subpixel_left_movement_and_large_pause_are_bounded(self):
        self.behavior.set_slow(True)
        start = self.pet.x()
        for _ in range(50):
            self.behavior.move_toward(0, .004)
        self.assertLess(self.pet.x(), start)
        start = self.pet.x()
        self.behavior.move_toward(0, 100.0)
        self.assertLessEqual(abs(self.pet.x() - start), 5)

    def test_all_one_shots_complete_and_repeat_from_the_first_frame(self):
        for state in ("wave", "celebrate", "jump", "stretch", "bow"):
            with self.subTest(state=state):
                self.behavior.play_one_shot(state)
                self.pet._advance_frame(200)
                self.behavior.play_one_shot(state)
                self.assertEqual((self.pet.frame_idx, self.pet._frame_elapsed), (0, 0))
                self.pet._advance_frame(sum(self.pet._animation_meta()["durations"]))
                self.behavior._tick()
                self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))

    def test_dance_finishes_a_whole_played_cycle_even_after_a_clock_jump(self):
        for slow in (False, True):
            with self.subTest(slow=slow):
                self.behavior.set_slow(slow)
                self.behavior.dance(4.8)
                cycle = sum(self.sprites.states["dance"]["durations"]) * self.pet.time_scale
                target = self.behavior._dance_cycle_target
                with patch("behavior.time.monotonic", return_value=self.behavior.mode_until + 100):
                    self.behavior._tick()
                    self.assertEqual(self.behavior.mode, "dance")
                self.pet._advance_frame(cycle * target - 1)
                self.behavior._tick()
                self.assertEqual(self.behavior.mode, "dance")
                self.pet._advance_frame(1)
                self.behavior._tick()
                self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))

    def test_thinking_preserves_dance_cycle_progress(self):
        self.behavior.dance(4.8)
        cycle = sum(self.sprites.states["dance"]["durations"])
        self.pet._advance_frame(cycle * 2 + 100)
        saved = (self.pet._completed_cycles, self.pet.frame_idx, self.pet._frame_elapsed)
        self.behavior.begin_thinking()
        self.pet._advance_frame(2000)
        self.behavior._finish_thinking()
        self.assertEqual((self.pet._completed_cycles, self.pet.frame_idx,
                          self.pet._frame_elapsed), saved)
        self.assertEqual(self.behavior.mode, "dance")

    def test_tour_never_overwrites_drag_or_fall_and_resumes_without_jump(self):
        tour = self.tour()
        tour.start()
        tour._tick.stop()
        for falling in (False, True):
            with self.subTest(falling=falling):
                self.pet._dragging = not falling
                self.behavior._fell = falling
                self.pet.set_state("drag")
                before = self.pet.pos()
                tour._last_tick_time -= 10
                tour._update()
                self.assertEqual(self.pet.pos(), before)
                self.assertEqual(self.pet.state, "drag")
        self.pet._dragging = False
        self.behavior._fell = False
        before = self.pet.pos()
        tour._last_tick_time -= 10
        tour._update()
        self.assertEqual(self.pet.pos(), before)

    def test_tour_elapsed_time_matches_wandering_and_arrival_resumes_idle(self):
        tour = self.tour()
        with patch.object(tour, "_anchor_x", return_value=320):
            now = [10.0]
            with patch("onboarding.time.monotonic", side_effect=lambda: now[0]):
                tour.start()
                tour._tick.stop()
                for _ in range(150):
                    now[0] += .030
                    self.pet._advance_frame(30)
                    tour._update()
                    if tour._arrived:
                        break
        self.assertTrue(tour._arrived)
        self.assertEqual(self.pet.x(), 320)
        if self.behavior.mode.startswith("transition:"):
            self.pet._advance_frame(sum(self.pet._animation_meta()["durations"]))
            tour._update()
        self.assertEqual(self.pet.state, "wave")
        self.pet._advance_frame(sum(self.sprites.states["wave"]["durations"]))
        tour._update()
        self.assertEqual(self.pet.state, "idle")

    def test_finishing_tour_during_fall_preserves_physics_owner(self):
        tour = self.tour()
        tour.start()
        self.behavior._fell = True
        self.behavior.mode = "fall"
        self.pet.set_state("drag")
        tour.finish()
        self.assertFalse(self.behavior.onboarding_active)
        self.assertEqual((self.behavior.mode, self.pet.state), ("fall", "drag"))

    def test_tour_next_and_previous_keep_the_full_direction_change_sequence(self):
        self.install_bridges()
        tour = self.tour()
        tour.steps = [{"text": "right"}, {"text": "left"}]
        now = [10.0]
        with patch("onboarding.time.monotonic", side_effect=lambda: now[0]), \
                patch.object(tour, "_anchor_x", side_effect=lambda step: 900 if tour.index == 0 else 0):
            tour.start()
            tour._tick.stop()
            self.pet._advance_frame(600)
            for _ in range(20):
                now[0] += .030
                self.pet._advance_frame(30)
                tour._update()
            for navigate, expected_left in ((tour.next, True), (tour.prev, False)):
                navigate()
                for _ in range(30):
                    now[0] += .030
                    tour._update()
                    if self.pet.state == "turn_front":
                        break
                self.assertEqual(self.pet.state, "turn_front")
                self.assertEqual(self.pet.facing_left, not expected_left)
                before = self.pet.pos()
                self.pet._advance_frame(600)
                now[0] += .030
                tour._update()
                self.assertEqual(self.pet.state, "turn_bridge")
                self.assertEqual(self.pet.facing_left, expected_left)
                self.assertEqual(self.pet.pos(), before)
                self.pet._advance_frame(600)
                now[0] += .030
                tour._update()
                self.assertEqual(self.pet.state, "walk")


if __name__ == "__main__":
    unittest.main()
