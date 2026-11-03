"""Multi-monitor geometry: union bounds, per-monitor floors, gap fallback."""
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

import pet as pet_module
from behavior import BehaviorController
from pet import ASSETS, PetWidget, SpriteBank

LEFT_FLOOR = 1039   # QRect(0, 0, 1920, 1040).bottom()
RIGHT_FLOOR = 1079  # QRect(1920, 0, 1920, 1080).bottom()


def mock_screen(rect):
    screen = MagicMock()
    screen.availableGeometry.return_value = rect
    return screen


class DualMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.sprites = SpriteBank(ASSETS)

    def setUp(self):
        # Office layout: primary left (taskbar floor 1039), secondary right
        # (floor 1079 — monitors rarely share the exact same work area).
        self.left = mock_screen(QRect(0, 0, 1920, 1040))
        self.right = mock_screen(QRect(1920, 0, 1920, 1080))
        self.screens = [self.left, self.right]

        def screen_at(point):
            for s in self.screens:
                if s.availableGeometry().contains(point):
                    return s
            return None

        self.gui = MagicMock()
        self.gui.screens.return_value = self.screens
        self.gui.screenAt.side_effect = screen_at
        self.gui.primaryScreen.return_value = self.left
        patcher = patch.object(pet_module, "QGuiApplication", self.gui)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pet = PetWidget(self.sprites)
        self.pet.tick.stop()
        self.addCleanup(self.pet.close)
        self.behavior = BehaviorController(self.pet)
        self.behavior.timer.stop()

    def test_screen_bounds_is_the_union_of_all_monitors(self):
        bounds = self.pet.screen_bounds()
        self.assertEqual(bounds.left(), 0)
        self.assertEqual(bounds.right(), 3839)
        self.assertEqual(bounds.bottom(), RIGHT_FLOOR)
        self.assertTrue(bounds.contains(self.left.availableGeometry()))
        self.assertTrue(bounds.contains(self.right.availableGeometry()))

    def test_ground_y_follows_the_monitor_under_the_pet(self):
        self.pet.move(400, 0)
        self.assertEqual(self.pet.ground_y(), LEFT_FLOOR)
        self.pet.move(2500, 0)
        self.assertEqual(self.pet.ground_y(), RIGHT_FLOOR)

    def test_current_screen_bounds_is_local_not_union(self):
        self.pet.move(2500, 0)
        self.assertEqual(self.pet.current_screen_bounds(), QRect(1920, 0, 1920, 1080))
        self.pet.move(400, 0)
        self.assertEqual(self.pet.current_screen_bounds(), QRect(0, 0, 1920, 1040))

    def test_position_between_monitors_falls_back_to_nearest_screen(self):
        # A real gap: right monitor sits 80px past the left's right edge.
        self.right.availableGeometry.return_value = QRect(2000, 0, 1920, 1080)
        self.pet.move(1980 - self.pet.width() // 2, 0)  # center in the gap, nearer right
        self.assertIsNone(self.gui.screenAt(self.pet.geometry().center()))
        self.assertEqual(self.pet.ground_y(), RIGHT_FLOOR)

    def test_walk_settles_onto_the_new_monitors_floor(self):
        self.pet.move(400, LEFT_FLOOR - self.pet.height())
        # Crossing onto the taller work area should sink the pet by 40px.
        self.pet.move(2500, self.pet.y())
        for _ in range(10):
            self.behavior._settle_ground()
        self.assertEqual(self.pet.y(), RIGHT_FLOOR - self.pet.height())

    def test_wander_targets_can_land_on_the_second_monitor(self):
        targets = set()
        for _ in range(50):
            self.behavior.wander()
            targets.add(self.behavior.target_x)
        bounds = self.pet.screen_bounds()
        self.assertTrue(all(bounds.left() <= x <= bounds.right() for x in targets))
        self.assertTrue(any(x >= 1920 for x in targets),
                        "wander never reaches the second monitor")

    def test_single_monitor_uses_only_that_screen(self):
        # Multi-monitor paths engage only when a second screen exists.
        self.screens[:] = [self.left]
        self.gui.screens.return_value = self.screens
        bounds = self.pet.screen_bounds()
        self.assertEqual(bounds, QRect(0, 0, 1920, 1040))
        self.pet.move(400, 0)
        self.assertEqual(self.pet.ground_y(), LEFT_FLOOR)

    def test_negative_origin_monitor_is_inside_bounds(self):
        # Second monitor positioned left of the primary (negative x origin).
        self.screens[1] = mock_screen(QRect(-1920, 0, 1920, 1040))
        self.left.availableGeometry.return_value = QRect(0, 0, 1920, 1040)
        bounds = self.pet.screen_bounds()
        self.assertEqual(bounds.left(), -1920)
        self.assertEqual(bounds.right(), 1919)
        self.pet.move(-1000, 0)
        self.assertEqual(self.pet.ground_y(), LEFT_FLOOR)


if __name__ == "__main__":
    unittest.main()
