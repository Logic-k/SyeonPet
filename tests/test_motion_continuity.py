"""Presentation and physical continuity through pauses, layout and landing."""
import math
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QApplication

from bubble import SpeechBubble
from pet import ASSETS, PetWidget, SpriteBank
from test_pet_motion import FakeBank


class MotionContinuityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pet = PetWidget(FakeBank(), display_size=32)
        self.pet.tick.stop()
        self.pet.motion_amp = 0
        self.pet.ground_y = lambda: 1000
        self.pet.screen_bounds = lambda: QRect(0, 0, 3000, 2000)

    def tearDown(self):
        self.pet.close()
        self.app.processEvents()

    def test_state_entry_does_not_consume_time_before_it_started(self):
        self.pet._last_tick = 98.0
        with patch("pet.time.monotonic", return_value=100.0):
            self.pet.set_state("wave")
            self.pet._on_tick()
        self.assertEqual((self.pet.frame_idx, self.pet._frame_elapsed), (0, 0))
        self.assertFalse(self.pet.one_shot_finished())

    def test_live_tick_presents_short_poses_in_order_after_long_stalls(self):
        meta = self.pet.sprites.states["wave"]
        meta["frames"].append(meta["frames"][0])
        meta["durations"] = [20, 20, 20]
        with patch("pet.time.monotonic", return_value=100.0):
            self.pet.set_state("wave")
        seen = [self.pet.frame_idx]
        for now in (110.0, 120.0):
            with patch("pet.time.monotonic", return_value=now):
                self.pet._on_tick()
            seen.append(self.pet.frame_idx)
            self.assertFalse(self.pet.one_shot_finished())
        self.assertEqual(seen, [0, 1, 2])
        with patch("pet.time.monotonic", return_value=130.0):
            self.pet._on_tick()
        self.assertTrue(self.pet.one_shot_finished())

    def test_pack_fallback_resets_presentation_clock_and_cycle_count(self):
        bank = FakeBank()
        bank.states.pop("wave")
        self.pet.set_state("wave")
        self.pet._completed_cycles = 4
        with patch.object(self.pet, "_load_wardrobe_pack", return_value=bank), \
                patch("pet.write_pack_choice"), patch("pet.time.monotonic", return_value=500.0):
            self.pet.set_wardrobe_pack("pack-" + "0" * 32)
            self.pet._on_tick()
        self.assertEqual((self.pet.state, self.pet.frame_idx, self.pet._completed_cycles),
                         ("idle", 0, 0))
        self.assertEqual(self.pet._frame_elapsed, 0)
        self.assertTrue(self.pet.one_shot_finished())

    def test_pack_switch_to_shorter_frames_does_not_carry_old_elapsed_time(self):
        bank = FakeBank()
        bank.states["idle"]["durations"] = [20, 20]
        self.pet._frame_elapsed = 90
        with patch.object(self.pet, "_load_wardrobe_pack", return_value=bank), \
                patch("pet.write_pack_choice"), patch("pet.time.monotonic", return_value=500.0):
            self.pet.set_wardrobe_pack("pack-" + "0" * 32)
            self.pet._on_tick()
        self.assertEqual((self.pet.frame_idx, self.pet._frame_elapsed), (0, 0))
        with patch("pet.time.monotonic", return_value=500.016):
            self.pet._on_tick()
        self.assertEqual(self.pet.frame_idx, 0)
        self.assertAlmostEqual(self.pet._frame_elapsed, 16)

    def test_completed_cycles_include_wraps_from_a_partial_frame(self):
        self.pet._advance_frame(150)
        self.assertEqual(self.pet._completed_cycles, 0)
        self.pet._advance_frame(650)
        self.assertEqual((self.pet._completed_cycles, self.pet.frame_idx,
                          self.pet._frame_elapsed), (4, 0, 0))
        self.pet.set_state("dance")
        self.assertEqual(self.pet._completed_cycles, 0)

    def test_fractional_duration_finishes_on_exact_cycle_boundary(self):
        self.pet.sprites.states["idle"]["durations"] = [91, 91]
        self.pet.time_scale = 1.7
        cycle = 182 * 1.7
        self.pet._advance_frame(cycle * 4 - 1)
        self.assertEqual(self.pet._completed_cycles, 3)
        self.pet._advance_frame(1)
        self.assertEqual((self.pet._completed_cycles, self.pet.frame_idx,
                          self.pet._frame_elapsed), (4, 0, 0))

    def test_packaged_recovery_sequences_keep_original_source_pixmaps(self):
        bank = SpriteBank(ASSETS)
        for state, sequence in (("sleep", (0, 1, 2, 3, 2, 1)),
                                ("liesleep", (0, 2, 4, 5, 3, 1)),
                                ("stretch", (0, 1, 2, 3, 2, 4, 5)),
                                ("bow", (0, 1, 2, 3, 2, 4, 5))):
            source = bank.states[state]
            playback = source["playback"]
            self.assertEqual(playback["source_indices"], sequence)
            for index, source_index in enumerate(sequence):
                self.assertIs(playback["frames"][index], source["frames"][source_index])
            if state in ("stretch", "bow"):
                self.assertEqual(sum(playback["durations"]), 1640)
            elif state == "liesleep":
                self.assertEqual(playback["durations"], [300, 300, 450, 300, 300, 450])
        self.assertEqual(bank.states["thinking"]["render_offset_y"], 11)

    def test_resting_pose_scale_preserves_source_canvas_and_floor(self):
        bank = SpriteBank(ASSETS)
        self.pet.sprites = bank
        self.pet.wardrobe_choice = "original"
        for state, scale in (("liesleep", .82), ("still", .90)):
            self.pet.set_state(state)
            source = bank.states[state]["frames"][0]
            source_key = source.cacheKey()
            rendered = self.pet._frame_pm(0)
            old_bounds = QRegion(source.mask()).boundingRect()
            new_bounds = QRegion(rendered.mask()).boundingRect()
            self.assertEqual(rendered.size(), source.size())
            self.assertEqual(source.cacheKey(), source_key)
            self.assertLessEqual(abs(old_bounds.bottom() - new_bounds.bottom()), 1)
            self.assertAlmostEqual(new_bounds.width() / old_bounds.width(), scale, delta=.015)

    def test_every_packaged_playback_pose_draws_inside_its_native_mask(self):
        bank = SpriteBank(ASSETS)
        self.pet.sprites = bank
        self.pet.wardrobe_choice = "original"
        self.pet._pm_cache.clear()
        self.pet._mask_cache.clear()
        for state in bank.states:
            self.pet.set_state(state)
            for index in range(len(self.pet._animation_meta()["frames"])):
                self.pet.frame_idx = index
                self.pet._update_mask()
                canvas = QPixmap(self.pet.size())
                canvas.fill(Qt.GlobalColor.transparent)
                painter = QPainter(canvas)
                self.pet._draw_scaled(painter, self.pet._cur_scaled_pm())
                painter.end()
                outside = QRegion(canvas.mask()).subtracted(self.pet.mask())
                self.assertTrue(outside.isEmpty(), (state, index))

    def test_render_offset_moves_drawing_geometry_and_hit_mask_together(self):
        self.pet.sprites.states["idle"]["render_offset_y"] = 3
        self.pet._update_mask()
        x, y, _, _ = self.pet._sprite_draw_rect()
        self.assertEqual(y, 3)
        self.assertFalse(self.pet.mask().contains(QPoint(x + 3, 8)))
        self.assertTrue(self.pet.mask().contains(QPoint(x + 3, 11)))
        image = QImage(self.pet.width(), self.pet.height(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        self.pet._draw_scaled(painter, self.pet._cur_scaled_pm())
        painter.end()
        self.assertEqual(image.pixelColor(x + 3, 8).alpha(), 0)
        self.assertGreater(image.pixelColor(x + 3, 11).alpha(), 0)

    def test_optional_bridges_reverse_existing_poses_and_keep_timing(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            for state in ("sleep_bridge", "turn_bridge"):
                folder = assets / "motion_bridges" / state
                folder.mkdir(parents=True)
                sheet = QPixmap(96, 16)
                sheet.fill(Qt.GlobalColor.transparent)
                painter = QPainter(sheet)
                for index in range(6):
                    painter.fillRect(index * 16 + 2, 2, 12, 12, Qt.GlobalColor.red)
                painter.end()
                self.assertTrue(sheet.save(str(folder / "sprite-sheet-alpha.png")))
                manifest = {
                    "frame_layout": {"rows": {state: [
                        {"x": i * 16, "y": 0, "w": 16, "h": 16} for i in range(6)]}},
                    "animation": {"rows": {state: {
                        "frames": 6, "loop": False, "durations_ms": [80, 90, 100, 110, 120, 130]}}},
                }
                (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bank = SpriteBank.__new__(SpriteBank)
            bank.states = {}
            bank._load_motion_bridges(assets)
        for source, reverse in (("sleep_bridge", "wake_bridge"), ("turn_bridge", "turn_front")):
            self.assertFalse(bank.states[reverse]["loop"])
            self.assertEqual(bank.states[reverse]["durations"], [130, 120, 110, 100, 90, 80])
            for index in range(6):
                self.assertIs(bank.states[reverse]["frames"][index], bank.states[source]["frames"][5 - index])

    def test_missing_bridge_rows_do_not_introduce_partial_transition_states(self):
        bank = SpriteBank.__new__(SpriteBank)
        bank.states = {"idle": self.pet.sprites.states["idle"]}
        with tempfile.TemporaryDirectory() as temporary:
            bank._load_motion_bridges(Path(temporary))
        self.assertEqual(set(bank.states), {"idle"})

    def test_bubble_appearance_and_disappearance_preserve_airborne_feet(self):
        self.pet.move(100, 800)
        self.pet.show()
        self.app.processEvents()
        bubble = SpeechBubble(self.pet)
        self.pet.bubble_widget = bubble
        feet = self.pet.y() + self.pet.height()
        bubble.show_temporary("공중에서도 위치를 유지해요", 100000)
        self.assertGreater(self.pet.height(), self.pet.base_height)
        self.assertEqual(self.pet.y() + self.pet.height(), feet)
        bubble.hide()
        self.assertEqual(self.pet.y() + self.pet.height(), feet)

    def test_slow_airborne_motion_keeps_fractional_displacement(self):
        self.pet.move(100, 100)
        self.pet.vx = 24
        for _ in range(12):
            self.pet.physics_step(1 / 120)
        self.assertEqual(self.pet.x(), 102)
        self.assertAlmostEqual(self.pet._physics_position.x(), 102.4)
        self.assertAlmostEqual(self.pet._physics_position.y(), 113.0)

    def test_external_move_resyncs_fractional_position(self):
        self.pet.move(100, 100)
        self.pet.vx = 24
        self.pet.physics_step(1 / 120)
        self.pet.move(300, 200)
        self.pet.physics_step(1 / 120)
        self.assertAlmostEqual(self.pet._physics_position.x(), 300.2)
        self.assertGreaterEqual(self.pet.y(), 200)

    def test_landing_slide_is_independent_of_timer_frequency(self):
        positions = []
        velocities = []
        for hz in (30, 60, 120):
            self.pet._physics_position = None
            self.pet.move(100, 1000 - self.pet.height())
            self.pet.vx, self.pet.vy = 120.0, 0.0
            for _ in range(hz):
                self.pet.physics_step(1 / hz)
            positions.append(self.pet._physics_position.x())
            velocities.append(self.pet.vx)
        self.assertLess(max(positions) - min(positions), 1e-8)
        self.assertLess(max(velocities) - min(velocities), 1e-8)
        self.assertAlmostEqual(positions[0], 100 + 120 * (1 - .8 ** 60) / (-60 * math.log(.8)))

    def test_fall_and_bounce_are_consistent_at_different_timer_rates(self):
        results = []
        for hz in (30, 60, 120):
            self.pet._physics_position = None
            self.pet.move(100, 900)
            self.pet.vx, self.pet.vy = 90.0, 80.0
            for _ in range(hz // 2):
                self.pet.physics_step(1 / hz)
            results.append((self.pet._physics_position.x(), self.pet._physics_position.y(),
                            self.pet.vx, self.pet.vy))
        for index in range(4):
            self.assertLess(max(row[index] for row in results) - min(row[index] for row in results), 1e-7)

    def test_final_contact_starts_one_landing_detail_not_one_per_slide_tick(self):
        self.pet.move(100, 1000 - self.pet.height() - 1)
        self.pet.vx, self.pet.vy = 120.0, 0.0
        with patch.object(self.pet, "play_transition") as transition:
            for _ in range(20):
                self.pet.physics_step(1 / 60)
        transition.assert_called_once_with("landing")

    def test_transition_recovers_even_after_a_one_shot_has_finished(self):
        self.pet.motion_amp = 1
        self.pet.set_state("wave")
        self.pet._advance_frame(1000)
        self.pet.play_transition("landing")
        with patch("pet.time.monotonic", return_value=self.pet._last_tick + .05):
            self.pet._on_tick()
        self.assertLess(self.pet._proc_fx()[1], 1)
        for _ in range(4):
            with patch("pet.time.monotonic", return_value=self.pet._last_tick + .05):
                self.pet._on_tick()
        self.assertIsNone(self.pet._transition_kind)
        self.assertEqual(self.pet._proc_fx(), (1, 1, 0, 0, 0))

    def test_reduced_motion_cancels_and_disables_transition(self):
        self.pet.motion_amp = 1
        self.pet.play_transition("walk_start")
        self.pet.set_reduced_motion(True)
        self.pet.play_transition("landing")
        self.assertIsNone(self.pet._transition_kind)
        self.assertEqual(self.pet._proc_fx(), (1, 1, 0, 0, 0))

    def test_transition_mask_covers_visible_transformed_pixels(self):
        self.pet.motion_amp = 2
        for kind in ("landing", "walk_start", "walk_stop"):
            self.pet.set_state("walk")
            self.pet.play_transition(kind)
            self.pet._transition_elapsed = 90
            self.pet._frame_elapsed = 30
            self.pet._update_mask()
            image = QImage(self.pet.width(), self.pet.height(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            sx, sy, angle, dx, dy = self.pet._proc_fx()
            x, y, w, h = self.pet._sprite_draw_rect()
            painter.translate(x + w / 2 + dx, y + h + dy)
            painter.rotate(angle)
            painter.scale(sx, sy)
            painter.translate(-x - w / 2, -y - h)
            self.pet._draw_scaled(painter, self.pet._cur_scaled_pm())
            painter.end()
            for y in range(image.height()):
                for x in range(image.width()):
                    if image.pixelColor(x, y).alpha() > 128:
                        self.assertTrue(self.pet.mask().contains(QPoint(x, y)), (kind, x, y))


if __name__ == "__main__":
    unittest.main()
