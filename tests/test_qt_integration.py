"""Qt offscreen smoke tests; these do not replace Windows desktop testing."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from behavior import BehaviorController
from bubble import SpeechBubble
from chat import ChatPanel
from onboarding import OnboardingTour
from pet import ASSETS, PetWidget, SpriteBank


class QtIntegrationSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.sprites = SpriteBank(ASSETS)

    def setUp(self):
        self.pet = PetWidget(self.sprites)
        self.behavior = BehaviorController(self.pet)
        # The tests drive state changes explicitly, without background ticks.
        self.pet.tick.stop()
        self.behavior.timer.stop()
        self.pet.show()
        self.app.processEvents()

    def tearDown(self):
        self.pet.close()
        self.app.processEvents()

    def mouse_event(self, kind, pos):
        pressed = kind == QEvent.Type.MouseButtonPress
        event = QMouseEvent(kind, QPointF(pos), QPointF(self.pet.mapToGlobal(pos)),
                            Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton if pressed else Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.pet, event)

    def test_prompt_conversation_is_the_single_default_view(self):
        chat = ChatPanel(self.pet, self.behavior, {})
        chat.prompt_conversation.input.setPlainText("정리할 요청")
        chat.show_prompt_near_pet()
        self.assertFalse(hasattr(chat, "tabs"))
        self.assertFalse(hasattr(chat, "brain"))
        chat.close()
        chat.show_near_pet()
        self.assertEqual(chat.prompt_conversation.input.toPlainText(), "정리할 요청")
        chat.close()

    def test_pet_actions_and_clicks(self):
        self.behavior.wander()
        self.finish_bridge()
        self.assertEqual((self.behavior.mode, self.pet.state), ("walk", "walk"))
        self.behavior.sleep()
        self.assertTrue(self.behavior.sleeping)
        self.finish_bridge()
        self.assertEqual(self.behavior.mode, "sleep")
        self.mouse_event(QEvent.Type.MouseButtonPress, QPoint(150, 80))
        self.mouse_event(QEvent.Type.MouseButtonRelease, QPoint(150, 80))
        self.finish_bridge()
        self.assertEqual(self.behavior.mode, "oneshot:idle")
        self.pet.double_clicked.emit()
        self.finish_bridge()
        self.assertEqual(self.behavior.mode, "sleep")
        self.behavior.dance()
        self.assertEqual((self.behavior.mode, self.pet.state), ("dance", "dance"))

    def finish_bridge(self):
        if self.behavior.mode.startswith("transition:"):
            self.assertIn(self.pet.state, ("sleep_bridge", "wake_bridge", "turn_bridge", "turn_front"))
            self.pet._advance_frame(sum(self.pet._animation_meta()["durations"]) * self.pet.time_scale)
            self.behavior._tick()
            self.assertFalse(self.behavior.mode.startswith("transition:"))

    def test_drag_and_gravity_settle(self):
        self.mouse_event(QEvent.Type.MouseButtonPress, QPoint(150, 80))
        self.assertTrue(self.pet._dragging)
        self.behavior._tick()
        self.assertEqual(self.behavior.mode, "drag")
        self.mouse_event(QEvent.Type.MouseButtonRelease, QPoint(150, 80))
        self.assertFalse(self.pet._dragging)
        self.pet.move(self.pet.x(), self.pet.ground_y() - self.pet.height() - 50)
        self.pet.vx = self.pet.vy = 0
        self.assertTrue(any(self.pet.physics_step(0.016) for _ in range(300)))

    def test_onboarding_navigation_and_finish(self):
        bubble = SpeechBubble(self.pet)
        self.pet.bubble_widget = bubble
        tour = OnboardingTour(self.pet, self.behavior, bubble, [
            {"title": "첫 단계", "text": "안내", "anchor": "left"},
            {"title": "다음 단계", "text": "안내", "anchor": "right"},
        ])
        tour.start()
        self.assertTrue(tour.active)
        self.assertTrue(self.behavior.onboarding_active)
        tour.next()
        self.assertEqual(tour.index, 1)
        tour.prev()
        self.assertEqual(tour.index, 0)
        tour.finish()
        self.assertFalse(tour.active)
        self.assertFalse(self.behavior.onboarding_active)

    def test_geometry_and_non_windows_click_through_branch(self):
        bounds = self.pet.screen_bounds()
        sprite = self.pet.sprite_rect_global()
        self.assertGreater(bounds.width(), 0)
        self.assertGreater(sprite.width(), 0)
        self.assertEqual(self.pet.ground_y(), bounds.bottom())
        # Offscreen cannot verify real Windows hit testing; cover the Qt branch.
        with patch("pet.sys.platform", "test-platform"):
            self.pet.set_click_through(True)
            self.assertTrue(self.pet.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            self.pet.set_click_through(False)
            self.assertFalse(self.pet.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))


if __name__ == "__main__":
    unittest.main()
