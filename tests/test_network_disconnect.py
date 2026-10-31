"""Exercise the UI and real transport boundary with synthetic network failure.

No real DNS, connection, saved user settings, clipboard, or external API is
used. The consent dialog is driven through its normal Qt finished signal.
"""
from __future__ import annotations

import os
import socket
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from behavior import BehaviorController
from optimizer_chat import PromptConversation
from pet import ASSETS, PetWidget, SpriteBank


class NetworkDisconnectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = PromptConversation()
        self.panel.show()
        self.app.processEvents()

    def tearDown(self):
        self._wait_idle()
        self.panel.close()
        self.app.processEvents()

    def _wait_idle(self):
        deadline = time.monotonic() + 5
        while self.panel.is_busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        self.assertFalse(self.panel.is_busy, "optimizer worker did not finish")

    def _accept_confirmation(self):
        dialog = self.panel._confirmation
        self.assertTrue(dialog.isVisible())
        self.assertIn("Vireonix", dialog.text())
        self.assertIn("전송", dialog.text())
        dialog.done(int(QMessageBox.StandardButton.Yes))
        self.app.processEvents()

    def test_dns_failure_then_explicit_retry_preserves_every_draft(self):
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("offline"))
        self.panel.input.setPlainText("원래 합성 업무 요청")
        self.panel.send.click()
        self._wait_idle()
        self.panel.result.setPlainText("사용자가 수정한 기존 결과")
        self.panel.input.setPlainText("새로운 합성 추가 조건")
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        transitions = []
        self.panel.busy_changed.connect(transitions.append)

        with patch("optimizer_ai.socket.getaddrinfo", side_effect=socket.gaierror("offline")) as dns, \
                patch("optimizer_ai.socket.create_connection", side_effect=AssertionError("unexpected connection")) as connect:
            self.panel.send.click()
            self.assertFalse(self.panel.is_busy)  # No work before consent.
            dns.assert_not_called()
            self._accept_confirmation()
            self._wait_idle()
            dns.assert_called_once()
            connect.assert_not_called()

        self.assertEqual(transitions, [True, False])
        self.assertEqual(self.panel.provider.currentData(), "vireonix")
        self.assertEqual(self.panel.original, "원래 합성 업무 요청")
        self.assertEqual(self.panel.input.toPlainText(), "새로운 합성 추가 조건")
        self.assertEqual(self.panel.result.toPlainText(), "사용자가 수정한 기존 결과")
        self.assertEqual(self.panel.revisions, [])
        self.assertIn("주소를 확인하지 못했습니다", self.panel.status.text())
        self.assertIn("인터넷 연결과 DNS 설정을 확인", self.panel.status.text())
        self.assertIn("다시 시도", self.panel.status.text())
        self.assertTrue(self.panel.send.isEnabled())

        with patch("optimizer_ai._send", return_value="합성 AI 결과") as send:
            self.panel.send.click()
            self.assertFalse(self.panel.is_busy)
            send.assert_not_called()  # A retry requires new, explicit consent.
            self._accept_confirmation()
            self._wait_idle()
            send.assert_called_once()
            payload = send.call_args.args[2]
            self.assertEqual(payload["original_request"], "원래 합성 업무 요청")
            self.assertEqual(payload["previous_result"], "사용자가 수정한 기존 결과")
            self.assertEqual(payload["revision"], "새로운 합성 추가 조건")
        self.assertEqual(self.panel.result.toPlainText(), "합성 AI 결과")
        self.assertEqual(self.panel.revisions, ["새로운 합성 추가 조건"])
        self.assertEqual(transitions, [True, False, True, False])

    def test_asleep_does_not_open_consent_or_start_dns(self):
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("수면 중 보존할 합성 입력")
        self.panel.set_sleep_paused(True)
        with patch.object(self.panel, "_confirm") as confirm, \
                patch("optimizer_ai.socket.getaddrinfo") as dns:
            self.assertFalse(self.panel.send.isEnabled())
            self.panel.submit()  # Also block programmatic submission.
            confirm.assert_not_called()
            dns.assert_not_called()
        self.assertEqual(self.panel.input.toPlainText(), "수면 중 보존할 합성 입력")
        self.panel.set_sleep_paused(False)
        self.assertTrue(self.panel.send.isEnabled())

    def test_dns_failure_ends_real_pet_thinking_after_visual_tail(self):
        pet = PetWidget(SpriteBank(ASSETS))
        behavior = BehaviorController(pet)
        pet.tick.stop()
        behavior.timer.stop()
        self.addCleanup(pet.close)
        behavior.wander()
        if pet.sprites.has("turn_bridge"):
            self.assertEqual((behavior.mode, pet.state), ("transition:walk", "turn_bridge"))
            before_turn = pet.pos()
            pet._advance_frame(sum(pet._animation_meta()["durations"]) * pet.time_scale)
            behavior._tick()
            self.assertEqual(pet.pos(), before_turn)
        self.assertEqual((behavior.mode, pet.state), ("walk", "walk"))
        walking_target = behavior.target_x
        observed = []

        def on_busy(busy):
            if busy:
                behavior.begin_thinking()
            else:
                behavior.end_thinking()
            observed.append((busy, behavior.mode))

        self.panel.busy_changed.connect(on_busy)
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("오프라인 연결 시험")
        with patch("optimizer_ai.socket.getaddrinfo", side_effect=socket.gaierror("offline")), \
                patch("optimizer_ai.socket.create_connection", side_effect=AssertionError("unexpected connection")):
            self.panel.send.click()
            self._accept_confirmation()
            self._wait_idle()

        self.assertEqual(observed, [(True, "thinking"), (False, "thinking")])
        self.assertTrue(behavior._thinking_finish_pending)
        deadline = time.monotonic() + 3
        while behavior.mode == "thinking" and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertEqual(behavior.mode, "walk")
        self.assertEqual(behavior.target_x, walking_target)
        self.assertFalse(behavior._thinking_finish_pending)
        self.assertEqual(self.panel.input.toPlainText(), "오프라인 연결 시험")
        self.assertIn("인터넷 연결과 DNS 설정을 확인", self.panel.status.text())


if __name__ == "__main__":
    unittest.main()
