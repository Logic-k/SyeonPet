"""Focused Qt checks for frame timing, alpha blending and still sleep."""
import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from pet import PetWidget


def frame(left: bool) -> QPixmap:
    image = QPixmap(32, 32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.fillRect(QRect(0 if left else 16, 8, 16, 16), QColor("red" if left else "blue"))
    # An overlapping opaque area verifies true alpha interpolation as well.
    painter.fillRect(QRect(12, 12, 8, 8), QColor("red" if left else "blue"))
    painter.end()
    return image


class FakeBank:
    def __init__(self):
        a, b = frame(True), frame(False)
        self.states = {
            "idle": {"frames": [a, b], "durations": [100, 100], "loop": True},
            "still": {"frames": [a, b], "durations": [100, 100], "loop": True},
            "wave": {"frames": [a, b], "durations": [100, 100], "loop": False},
            "walk": {"frames": [a, b], "durations": [100, 100], "loop": True},
            "dance": {"frames": [a, b], "durations": [100, 100], "loop": True},
        }

    def has(self, state):
        return state in self.states


class PetMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pet = PetWidget(FakeBank(), display_size=32)
        self.pet.tick.stop()
        self.pet.motion_amp = 0

    def tearDown(self):
        self.pet.close()
        self.app.processEvents()

    def test_fade_mask_includes_both_frames_and_shrinks_after_transition(self):
        self.pet.crossfade = True
        self.pet.BLEND_MS = 35
        self.pet._frame_elapsed = 80
        self.pet._update_mask()
        x = (self.pet.win_width - 32) // 2
        self.assertTrue(self.pet.mask().contains(QPoint(x + 3, 15)))
        self.assertTrue(self.pet.mask().contains(QPoint(x + 28, 15)))
        self.pet._advance_frame(21)
        self.assertEqual(self.pet.frame_idx, 1)
        self.assertFalse(self.pet.mask().contains(QPoint(x + 3, 15)))
        self.assertTrue(self.pet.mask().contains(QPoint(x + 28, 15)))

    def test_dissolve_preserves_alpha_in_overlapping_opaque_area(self):
        image = self.pet._crossfaded_pixmap(0, 1, .5).toImage()
        overlap = image.pixelColor(15, 15)
        self.assertGreaterEqual(overlap.alpha(), 250)
        self.assertTrue(120 <= overlap.red() <= 135)
        self.assertTrue(120 <= overlap.blue() <= 135)
        self.assertTrue(120 <= image.pixelColor(3, 15).alpha() <= 135)

    def test_still_state_has_no_animation_or_procedural_transform(self):
        self.pet.motion_amp = 1
        self.pet.crossfade = True
        self.pet.set_state("still")
        self.pet._advance_frame(10000)
        self.assertEqual(self.pet.frame_idx, 0)
        self.assertEqual(self.pet._proc_fx(), (1.0, 1.0, 0.0, 0.0, 0.0))
        self.assertEqual(self.pet._blend_info(), (None, 0.0))

    def test_large_elapsed_time_is_bounded_and_keeps_phase(self):
        start = time.monotonic()
        self.pet._advance_frame(1_000_000_150)
        self.assertLess(time.monotonic() - start, .5)
        self.assertEqual(self.pet.frame_idx, 1)
        self.assertEqual(self.pet._frame_elapsed, 50)

    def test_one_shot_completes_after_long_stall_and_stays_done(self):
        self.pet.set_state("wave")
        self.pet._advance_frame(10000)
        self.assertEqual(self.pet.frame_idx, 1)
        self.assertTrue(self.pet.one_shot_finished())
        self.pet._advance_frame(10000)
        self.assertEqual(self.pet.frame_idx, 1)
        self.assertEqual(self.pet._frame_elapsed, 0)

    def test_zero_blend_and_invalid_time_do_not_divide_or_step(self):
        self.pet.BLEND_MS = 0
        self.assertEqual(self.pet._blend_info(), (None, 0.0))
        self.pet._advance_frame(float("inf"))
        self.assertEqual(self.pet.frame_idx, 0)
        self.assertFalse(self.pet.physics_step(float("inf")))

    def test_motion_mask_covers_transformed_visible_pixels_and_remains_local(self):
        self.pet.motion_amp = 2
        for state in ("walk", "dance"):
            self.pet.set_state(state)
            for elapsed in range(0, 100, 10):
                self.pet._frame_elapsed = elapsed
                self.pet._update_mask()
                image = QImage(self.pet.width(), self.pet.height(), QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                painter = QPainter(image)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                sx, sy, angle, dx, dy = self.pet._proc_fx()
                rx, ry, rw, rh = self.pet._sprite_draw_rect()
                painter.translate(rx + rw / 2 + dx, ry + rh + dy)
                painter.rotate(angle)
                painter.scale(sx, sy)
                painter.translate(-rx - rw / 2, -ry - rh)
                self.pet._draw_scaled(painter, self.pet._cur_scaled_pm())
                painter.end()
                region = self.pet.mask()
                self.assertFalse(region.contains(QPoint(0, 0)))
                for y in range(image.height()):
                    for x in range(image.width()):
                        if image.pixelColor(x, y).alpha() > 128:
                            self.assertTrue(region.contains(QPoint(x, y)), (state, elapsed, x, y))


if __name__ == "__main__":
    unittest.main()
