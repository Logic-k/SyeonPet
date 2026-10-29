"""Real Qt mouse events for click jitter, sleep wake, and drag handling."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from behavior import BehaviorController
from pet import ASSETS, PetWidget, SpriteBank


class PetClickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.sprites = SpriteBank(ASSETS)

    def setUp(self):
        self.pet = PetWidget(self.sprites)
        self.behavior = BehaviorController(self.pet)
        self.pet.tick.stop()
        self.behavior.timer.stop()
        self.clicks = []
        self.pet.clicked.connect(lambda: self.clicks.append(True))
        self.start = self.pet.mapToGlobal(QPoint(150, 80))

    def tearDown(self):
        self.pet.close()
        self.app.processEvents()

    def event(self, kind, offset, at):
        global_pos = self.start + QPoint(*offset)
        local_pos = self.pet.mapFromGlobal(global_pos)
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
        held = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
        event = QMouseEvent(kind, QPointF(local_pos), QPointF(global_pos), button,
                            held, Qt.KeyboardModifier.NoModifier)
        with patch("pet.time.monotonic", return_value=at):
            QApplication.sendEvent(self.pet, event)

    def start_drag(self):
        self.event(QEvent.Type.MouseButtonPress, (0, 0), 100.0)
        # A press lasting more than one behavior tick is temporarily 'drag'.
        with patch("behavior.time.monotonic", return_value=100.02):
            self.behavior._tick()

    def test_fast_and_slow_small_jitter_are_clicks_and_wake_sleep(self):
        for move_time in (100.021, 101.0):
            with self.subTest(move_time=move_time):
                self.behavior.still_sleep()
                self.clicks.clear()
                initial_position = self.pet.pos()
                self.start_drag()
                self.event(QEvent.Type.MouseMove, (2, 1), move_time)
                self.event(QEvent.Type.MouseButtonRelease, (2, 1), move_time + .001)
                self.assertEqual(self.clicks, [True])
                self.assertFalse(self.behavior.fully_asleep)
                self.assertFalse(self.behavior._fell)
                self.assertEqual((self.pet.vx, self.pet.vy), (0.0, 0.0))
                self.assertEqual(self.pet.pos(), initial_position)

    def test_slow_and_fast_real_drags_do_not_click(self):
        for duration in (.01, 1.0):
            with self.subTest(duration=duration):
                self.behavior.still_sleep()
                self.clicks.clear()
                self.start_drag()
                self.event(QEvent.Type.MouseMove, (30, 0), 100.02 + duration)
                self.event(QEvent.Type.MouseButtonRelease, (30, 0), 100.021 + duration)
                self.assertEqual(self.clicks, [])
                self.assertTrue(self.behavior.fully_asleep)
                self.assertTrue(self.behavior._fell)
                self.assertGreater(self.pet.vx, 0)
                self.assertLessEqual(self.pet.vx, self.pet.MAX_THROW_SPEED)

    def test_drag_out_and_back_is_not_a_click(self):
        self.start_drag()
        self.event(QEvent.Type.MouseMove, (30, 0), 100.03)
        self.event(QEvent.Type.MouseMove, (0, 0), 100.04)
        self.event(QEvent.Type.MouseButtonRelease, (0, 0), 100.05)
        self.assertEqual(self.clicks, [])
        self.assertTrue(self.behavior._fell)

    def test_release_without_move_event_still_distinguishes_drag(self):
        self.start_drag()
        self.event(QEvent.Type.MouseButtonRelease, (8, 0), 100.04)
        self.assertEqual(self.clicks, [])
        self.assertTrue(self.behavior._fell)

    def test_double_click_still_runs_configured_action(self):
        self.behavior.idle()
        self.event(QEvent.Type.MouseButtonDblClick, (0, 0), 100.0)
        self.assertEqual(self.behavior.mode, "sleep")

    def tick_until_landed(self):
        self.behavior._last_tick_time = 100.0
        for i in range(1, 401):
            with patch("behavior.time.monotonic", return_value=100.0 + i * .016):
                self.behavior._tick()
            if not self.behavior._fell:
                break
        self.assertFalse(self.behavior._fell, "pet did not settle after being released")
        self.assertEqual(self.pet.y(), self.pet.ground_y() - self.pet.height())

    def test_quick_drag_between_timer_ticks_falls_and_restores_full_sleep(self):
        self.behavior.still_sleep()
        original = self.pet.pos()
        self.event(QEvent.Type.MouseButtonPress, (0, 0), 100.0)
        self.event(QEvent.Type.MouseMove, (0, -100), 100.01)
        self.event(QEvent.Type.MouseButtonRelease, (0, -100), 100.02)
        self.assertEqual(self.pet.pos().y(), original.y() - 100)
        self.assertTrue(self.behavior._fell)
        self.assertTrue(self.behavior.fully_asleep)
        self.tick_until_landed()
        self.assertEqual((self.behavior.mode, self.pet.state), ("deepsleep", "still"))
        self.assertTrue(self.behavior.fully_asleep)

    def test_release_only_drag_starts_fall_between_timer_ticks(self):
        self.behavior.idle()
        original = self.pet.pos()
        self.event(QEvent.Type.MouseButtonPress, (0, 0), 100.0)
        self.event(QEvent.Type.MouseButtonRelease, (0, -100), 100.01)
        self.assertEqual(self.pet.pos().y(), original.y() - 100)
        self.assertTrue(self.behavior._fell)
        self.tick_until_landed()
        self.assertEqual(self.behavior.mode, "idle")

    def test_waking_during_fast_drag_remains_awake_after_landing(self):
        self.behavior.still_sleep()
        self.event(QEvent.Type.MouseButtonPress, (0, 0), 100.0)
        self.event(QEvent.Type.MouseMove, (0, -100), 100.01)
        self.assertTrue(self.behavior.fully_asleep)
        self.behavior.wake()
        self.assertFalse(self.behavior.fully_asleep)
        self.event(QEvent.Type.MouseButtonRelease, (0, -100), 100.02)
        self.assertTrue(self.behavior._fell)
        self.tick_until_landed()
        self.assertFalse(self.behavior.fully_asleep)
        self.assertEqual(self.behavior.mode, "idle")


if __name__ == "__main__":
    unittest.main()
