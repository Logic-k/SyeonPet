"""Sleep policy and animation timing regression tests with an isolated Qt app."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from behavior import BehaviorController
from chat import ChatPanel
from pet import ASSETS, PetWidget, SpriteBank


class SleepAndMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.sprites = SpriteBank(ASSETS)

    def setUp(self):
        self.pet = PetWidget(self.sprites)
        self.behavior = BehaviorController(self.pet)
        self.pet.tick.stop()
        self.behavior.timer.stop()
        self.panel = ChatPanel(self.pet, self.behavior, {})

    def tearDown(self):
        self.panel.close()
        self.pet.close()
        self.app.processEvents()

    def send_mouse(self, kind, pos=QPoint(150, 80)):
        pressed = kind == QEvent.Type.MouseButtonPress
        event = QMouseEvent(kind, QPointF(pos), QPointF(self.pet.mapToGlobal(pos)),
                            Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton if pressed else Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.pet, event)

    def test_full_sleep_pauses_prompt_sends_and_wake_preserves_drafts(self):
        prompt = self.panel.prompt_conversation
        prompt.input.setPlainText("작성 중인 정리 요청")
        prompt.result.setPlainText("수정 중인 결과")
        self.behavior.still_sleep()
        self.assertTrue(self.behavior.fully_asleep)
        self.assertFalse(prompt.send.isEnabled())
        self.assertFalse(self.panel.sleep_notice.isHidden())
        with patch("optimizer_chat.optimize_ai") as ai:
            prompt.submit()
            ai.assert_not_called()
        self.panel.wake_button.click()
        self.assertFalse(self.behavior.fully_asleep)
        self.assertTrue(prompt.send.isEnabled())
        self.assertEqual(prompt.input.toPlainText(), "작성 중인 정리 요청")
        self.assertEqual(prompt.result.toPlainText(), "수정 중인 결과")

    def test_other_sleep_modes_are_not_full_sleep(self):
        self.behavior.still_sleep()
        self.behavior.deep_sleep()
        self.assertFalse(self.behavior.fully_asleep)
        self.assertTrue(self.panel.prompt_conversation.send.isEnabled())
        self.behavior.sleep()
        self.assertTrue(self.panel.prompt_conversation.send.isEnabled())

    def test_delayed_external_confirmation_cannot_send_after_sleep(self):
        prompt = self.panel.prompt_conversation
        prompt.input.setPlainText("보존할 입력")
        self.behavior.still_sleep()
        with patch("optimizer_chat.threading.Thread") as thread:
            prompt._submit_confirmed("보존할 입력", "보존할 입력", "", "vireonix", "", "", "", "auto", "balanced", [])
            thread.assert_not_called()
        self.assertEqual(prompt.input.toPlainText(), "보존할 입력")
        self.assertFalse(prompt.send.isEnabled())

    def test_inflight_result_completes_without_reenabling_send(self):
        prompt = self.panel.prompt_conversation
        prompt._pending = ("원문", "원문", "", "offline")
        prompt._set_busy(True)
        self.behavior.still_sleep()
        prompt._finish({"result": "완료된 결과", "notes": [], "mode": "offline"})
        self.assertEqual(prompt.result.toPlainText(), "완료된 결과")
        self.assertFalse(prompt.send.isEnabled())
        self.panel.wake_button.click()
        self.assertTrue(prompt.send.isEnabled())

    def test_dragging_and_landing_does_not_wake_full_sleep(self):
        self.behavior.still_sleep()
        self.pet._dragging = True
        self.behavior._tick()
        self.assertEqual(self.behavior._pre_fall_mode, "deepsleep")
        self.pet._dragging = False
        with patch.object(self.pet, "physics_step", return_value=True):
            self.behavior._tick()
        self.assertTrue(self.behavior.fully_asleep)
        self.assertEqual((self.behavior.mode, self.pet.state), ("deepsleep", "still"))

    def test_regular_sleep_restored_after_drag(self):
        self.behavior.sleep()
        self.pet._dragging = True
        self.behavior._tick()
        self.pet._dragging = False
        with patch.object(self.pet, "physics_step", return_value=True):
            self.behavior._tick()
        self.assertEqual(self.behavior.mode, "sleep")
        self.assertFalse(self.behavior.fully_asleep)

    def test_held_click_wakes_sleep_without_leaving_fall_pending(self):
        for sleep_method in (self.behavior.still_sleep, self.behavior.sleep,
                             self.behavior.deep_sleep):
            with self.subTest(sleep_method=sleep_method.__name__):
                sleep_method()
                self.send_mouse(QEvent.Type.MouseButtonPress)
                self.behavior._tick()  # a timer tick passes during the press
                self.assertEqual(self.behavior.mode, "drag")
                self.send_mouse(QEvent.Type.MouseButtonRelease)
                self.assertFalse(self.behavior.fully_asleep)
                self.assertEqual(self.behavior.mode, "oneshot:idle")
                self.assertFalse(self.behavior._fell)
                self.assertTrue(self.panel.prompt_conversation.send.isEnabled())
                self.behavior._tick()
                self.assertEqual(self.behavior.mode, "oneshot:idle")

    def test_held_awake_click_runs_configured_action(self):
        self.behavior.idle()
        self.send_mouse(QEvent.Type.MouseButtonPress)
        self.behavior._tick()
        self.send_mouse(QEvent.Type.MouseButtonRelease)
        self.assertEqual((self.behavior.mode, self.pet.state), ("oneshot:idle", "wave"))
        self.assertFalse(self.behavior._fell)

    def test_explicit_wake_during_fall_is_not_reversed_on_landing(self):
        self.behavior.still_sleep()
        self.pet._dragging = True
        self.behavior._tick()
        self.pet._dragging = False
        with patch.object(self.pet, "physics_step", side_effect=(False, True)):
            self.behavior._tick()
            self.assertEqual(self.behavior.mode, "fall")
            self.panel.wake_button.click()
            self.assertFalse(self.behavior.fully_asleep)
            self.behavior._tick()
        self.assertEqual(self.behavior.mode, "idle")
        self.assertFalse(self.behavior.fully_asleep)

    def test_explicit_wake_while_dragging_is_not_reversed_on_landing(self):
        self.behavior.still_sleep()
        self.pet._dragging = True
        self.behavior._tick()
        self.panel.wake_button.click()
        self.behavior._tick()
        self.pet._dragging = False
        with patch.object(self.pet, "physics_step", return_value=True):
            self.behavior._tick()
        self.assertEqual(self.behavior.mode, "idle")
        self.assertFalse(self.behavior.fully_asleep)

    def test_elapsed_pause_is_bounded_for_walk(self):
        self.behavior.mode = "walk"
        self.behavior.target_x = self.pet.x() + 500
        self.behavior._last_tick_time = 1.0
        start = self.pet.x()
        with patch("behavior.time.monotonic", return_value=100.0):
            self.behavior._tick()
        self.assertLessEqual(self.pet.x() - start, 10)

    def test_auto_sleep_count_survives_normal_wander_arrival(self):
        self.behavior.idle()
        with patch("behavior.random.random", return_value=0.8):
            for count in range(1, 4):
                self.behavior.mode_until = 0
                self.behavior._tick()
                self.assertEqual(self.behavior.mode, "walk")
                self.behavior.target_x = self.pet.x()
                self.behavior._tick()
                self.assertEqual(self.behavior.idle_ticks, count)
        self.behavior.mode_until = 0
        with patch("behavior.random.random", return_value=0.1):
            self.behavior._tick()
        self.assertEqual(self.behavior.mode, "sleep")


if __name__ == "__main__":
    unittest.main()
