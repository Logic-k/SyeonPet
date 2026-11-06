"""Unit and integration tests for SyeonPet v0.2.0 Workspace Hub and Pet/Behavior enhancements."""
from __future__ import annotations

import ctypes
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from behavior import BehaviorController
from bubble import SpeechBubble
from chat import ChatPanel
from onboarding import OnboardingTour
from pet import ASSETS, PetWidget, SpriteBank
from syeonpet.pet.companion_care import PETTING_LINES, PettingTracker, paint_hearts
from syeonpet.pet.quick_bar import QuickActionBar, TimerBadge, format_badge_text


class HubAndPetV020Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.sprites = SpriteBank(ASSETS)

    def setUp(self):
        self.pet = PetWidget(self.sprites)
        self.pet.tick.stop()
        self.bubble = SpeechBubble(self.pet)
        self.pet.bubble_widget = self.bubble
        self.behavior = BehaviorController(self.pet)
        self.behavior.timer.stop()

    def tearDown(self):
        self.pet.close()
        self.bubble.close()
        self.app.processEvents()

    # 1. Petting Tracker & Heart Particles
    def test_petting_tracker_feed_point_and_stroke_detection(self):
        tracker = PettingTracker()
        # Out-of-bounds lower area should never trigger
        self.assertFalse(tracker.feed_point(50, 70, 100, now=10.0)[0])

        # Gentle strokes in upper head area (y = 20 < 100 * 0.55)
        self.assertFalse(tracker.feed_point(10, 20, 100, now=10.0)[0])
        self.assertFalse(tracker.feed_point(60, 20, 100, now=10.1)[0])
        self.assertFalse(tracker.feed_point(15, 20, 100, now=10.2)[0])
        triggered, line = tracker.feed_point(65, 20, 100, now=10.3)
        self.assertTrue(triggered)
        self.assertIn(line, PETTING_LINES)
        self.assertGreaterEqual(len(tracker.particles), 5)

        # Particle advancement and expiry
        initial_count = len(tracker.particles)
        tracker.update_particles(0.1)
        self.assertEqual(len(tracker.particles), initial_count)
        tracker.update_particles(2.0)
        self.assertEqual(len(tracker.particles), 0)

    def test_pet_widget_mouse_move_triggers_petting_and_hearts(self):
        received_lines = []
        self.pet.petted.connect(received_lines.append)
        rx, ry, rw, rh = self.pet._sprite_draw_rect()

        # Stroke back and forth over upper head
        for offset_x in (10, 60, 15, 65):
            pos = QPointF(rx + offset_x, ry + 15)
            evt = QMouseEvent(QEvent.Type.MouseMove, pos, Qt.MouseButton.NoButton,
                              Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
            self.pet.mouseMoveEvent(evt)

        self.assertEqual(len(received_lines), 1)
        self.assertIn(received_lines[0], PETTING_LINES)
        self.assertGreaterEqual(len(self.pet.heart_particles), 5)

        # Paint hearts should execute cleanly
        pix = QPixmap(200, 200)
        painter = QPainter(pix)
        paint_hearts(painter, self.pet.heart_particles)
        painter.end()

    def _hover(self, dy_ratio: float):
        rx, ry, rw, rh = self.pet._sprite_draw_rect()
        pos = QPointF(rx + rw / 2, ry + rh * dy_ratio)
        self.pet.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, pos, Qt.MouseButton.NoButton,
                                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

    def _hand_shown(self) -> bool:
        return (self.pet.testAttribute(Qt.WidgetAttribute.WA_SetCursor)
                and self.pet.cursor().shape() == Qt.CursorShape.OpenHandCursor)

    def test_open_hand_shows_only_over_the_head_and_clears(self):
        self._hover(0.2)
        self.assertTrue(self._hand_shown())
        self._hover(0.8)  # body and feet: not a petting spot
        self.assertFalse(self._hand_shown())
        self._hover(0.2)
        self.pet.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertFalse(self._hand_shown())
        self._hover(0.2)
        rx, ry, rw, rh = self.pet._sprite_draw_rect()
        press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(rx + rw / 2, ry + 10), QPointF(rx + rw / 2, ry + 10),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.pet.mousePressEvent(press)
        self.assertFalse(self._hand_shown())
        self.pet._dragging = False
        self._hover(0.2)
        self.pet.set_reduced_motion(True)
        self.assertFalse(self._hand_shown())
        self._hover(0.2)
        self.assertFalse(self._hand_shown())
        self.pet.set_reduced_motion(False)
        self.behavior.onboarding_active = True
        self._hover(0.2)
        self.assertFalse(self._hand_shown())
        self.behavior.onboarding_active = False
        self._hover(0.2)
        self.assertTrue(self._hand_shown())
        # The bubble keeps a normal pointer instead of inheriting the hand.
        self.assertEqual(self.bubble.cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.assertTrue(self.bubble.testAttribute(Qt.WidgetAttribute.WA_SetCursor))

    def test_petting_plays_the_smiling_nuzzle_once_without_waking_or_interrupting(self):
        self.assertTrue(self.sprites.has("nuzzle"))
        self.behavior.idle()
        self.pet.petted.emit("")
        self.assertEqual(self.pet.state, "nuzzle")
        self.assertEqual(self.behavior.mode, "oneshot:idle")
        self.pet.frame_idx = 3
        self.pet.petted.emit("")  # still nuzzling: no restart
        self.assertEqual((self.pet.state, self.pet.frame_idx), ("nuzzle", 3))
        for _ in range(20):
            self.pet._advance_frame(400)
        self.assertTrue(self.pet.one_shot_finished())
        self.behavior._tick()
        self.assertEqual(self.pet.state, "idle")
        self.behavior.sleep()
        self.pet.petted.emit("")
        self.assertNotEqual(self.pet.state, "nuzzle")
        self.behavior.wake()
        self.behavior.idle()
        self.behavior.onboarding_active = True
        self.pet.petted.emit("")
        self.assertNotEqual(self.pet.state, "nuzzle")
        self.behavior.onboarding_active = False
        self.pet.set_reduced_motion(True)
        self.pet.petted.emit("")
        self.assertNotEqual(self.pet.state, "nuzzle")
        self.pet.set_reduced_motion(False)

    def test_a_stroke_never_cuts_a_click_wave_or_a_dance(self):
        self.behavior.idle()
        self.behavior.play_one_shot("wave")
        self.pet.petted.emit("")
        self.assertEqual(self.pet.state, "wave")
        self.behavior.dance(2.0)
        self.pet.petted.emit("")
        self.assertEqual(self.pet.state, "dance")

    def test_an_alert_ends_the_nuzzle_instead_of_being_dropped(self):
        from main import show_companion_message
        self.behavior.idle()
        self.pet.petted.emit("")
        self.assertEqual(self.pet.state, "nuzzle")
        self.assertTrue(show_companion_message(self.behavior, self.bubble, True, "휴식 끝!"))
        self.assertEqual((self.pet.state, self.behavior.mode), ("idle", "idle"))

    def test_the_timer_badge_is_not_part_of_the_head(self):
        self.pet.set_timer_badge("🍅 24:50")
        rx, ry, rw, _rh = self.pet._sprite_draw_rect()
        badge = self.pet._badge_rect(rx, ry, rw)
        self.pet.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPointF(badge.center()), Qt.MouseButton.NoButton,
                                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        self.assertFalse(self._hand_shown())
        self.pet.set_timer_badge("")
        self._hover(0.2)
        self.assertTrue(self._hand_shown())

    def test_stroking_the_head_makes_syeon_nuzzle(self):
        self.behavior.idle()
        rx, ry, rw, rh = self.pet._sprite_draw_rect()
        for offset_x in (10, 60, 15, 65):
            self.pet.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPointF(rx + offset_x, ry + 15),
                                                Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                                                Qt.KeyboardModifier.NoModifier))
        self.assertEqual(self.pet.state, "nuzzle")
        self.assertTrue(self._hand_shown())

    # 2. Timer Badge Formatting and Rendering
    def test_format_badge_text_and_pet_badge_paint(self):
        focus_formatted = format_badge_text("집중", 1495)
        self.assertEqual(focus_formatted, "🍅 24:55")

        rest_formatted = format_badge_text("휴식", 300)
        self.assertEqual(rest_formatted, "☕ 05:00")

        self.pet.set_timer_badge(focus_formatted)
        self.assertEqual(self.pet.timer_badge_text, focus_formatted)

        # Check badge painting inside bounds
        pix = QPixmap(200, 200)
        painter = QPainter(pix)
        TimerBadge.paint(painter, QRect(10, 10, 88, 22), focus_formatted)
        painter.end()

        # Clear badge
        self.pet.set_timer_badge("")
        self.assertEqual(self.pet.timer_badge_text, "")

    # 3. Click-Through Mode & Escape Safety Hotkey
    def test_click_through_toggle_and_escape(self):
        changes = []
        self.pet.click_through_changed.connect(changes.append)

        self.pet.set_click_through(True)
        self.assertTrue(self.pet._click_through)
        self.assertEqual(changes, [True])

        # Test restore
        self.pet.restore_click_through()
        self.assertFalse(self.pet._click_through)
        self.assertEqual(changes, [True, False])

        # Test escape hotkey Ctrl+Alt+S
        self.pet.set_click_through(True)
        if hasattr(ctypes, "windll"):
            with patch.object(ctypes.windll.user32, "GetAsyncKeyState", return_value=0x8000):
                self.pet._check_click_through_escape()
                self.assertFalse(self.pet._click_through)
        else:
            self.pet.restore_click_through()
            self.assertFalse(self.pet._click_through)

    # 4. ChatPanel 4-Section Workspace Hub
    def test_chat_panel_sections_and_switching(self):
        cfg = {"font_size": 13}
        panel = ChatPanel(self.pet, self.behavior, cfg)
        try:
            self.assertEqual(panel.section_stack.count(), 4)
            self.assertIn("work", panel.section_buttons)
            self.assertIn("finder", panel.section_buttons)
            self.assertIn("bag", panel.section_buttons)
            self.assertIn("prompt", panel.section_buttons)

            # Test section switching
            panel.open_section("finder")
            self.assertEqual(panel.section_stack.currentIndex(), 1)

            panel.open_section("bag")
            self.assertEqual(panel.section_stack.currentIndex(), 2)

            panel.open_section("prompt")
            self.assertEqual(panel.section_stack.currentIndex(), 3)

            panel.open_section("work")
            self.assertEqual(panel.section_stack.currentIndex(), 0)

            # Test font size combo
            panel.font_size_combo.setCurrentText("크게")
            self.assertEqual(panel.font_size_combo.currentText(), "크게")

            # Test pin button toggle
            panel.pin_button.setChecked(True)
            self.assertTrue(panel.pin_button.isChecked())
            panel.pin_button.setChecked(False)
            self.assertFalse(panel.pin_button.isChecked())
        finally:
            panel.close()

    # 5. Companion Praise Signal Emission
    def test_companion_praise_signal(self):
        panel = ChatPanel(self.pet, self.behavior, {})
        praise_messages = []
        panel.companion_praise.connect(praise_messages.append)
        try:
            panel.companion_praise.emit("오늘 할 일을 하나 멋지게 끝냈어요! 훌륭해요!")
            self.assertEqual(praise_messages, ["오늘 할 일을 하나 멋지게 끝냈어요! 훌륭해요!"])
        finally:
            panel.close()

    # 6. Stationary Onboarding Tour
    def test_stationary_onboarding_default(self):
        tour = OnboardingTour(self.pet, self.behavior, self.bubble, [
            {"anchor": "bottom-right", "text": "안녕! 션이에요.", "pose": "wave", "extra_motion": None}
        ])
        self.assertTrue(tour._is_stationary)
        self.behavior.mode = "idle"
        tour.start()
        self.assertTrue(tour.active)
        self.assertEqual(tour.index, 0)
        tour.finish()
        self.assertFalse(tour.active)

    # 7. QuickActionBar Pill Toolbar Integration
    def test_quick_action_bar_interactions(self):
        bar = QuickActionBar()
        try:
            work_opened = []
            bar.open_work.connect(lambda: work_opened.append(True))
            bar.btn_work.click()
            self.assertEqual(work_opened, [True])

            bar.set_timer_active(True)
            self.assertTrue(bar._timer_active)
            bar.set_timer_active(False)
            self.assertFalse(bar._timer_active)

            # Test positioning near pet
            pet_geom = QRect(500, 500, 168, 168)
            bounds = QRect(0, 0, 1920, 1080)
            bar.show_near_pet(pet_geom, bounds)
            self.assertFalse(bar.isHidden())
            self.assertLessEqual(bar.y(), pet_geom.top())
        finally:
            bar.close()


if __name__ == "__main__":
    unittest.main()
