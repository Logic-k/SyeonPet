"""Unit tests for DirectWorkWidget in syeonpet.chat.direct_work_ui."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from syeonpet.chat.direct_work_ui import DirectWorkWidget, CHIPS


class DirectWorkWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = DirectWorkWidget()
        self.widget.show()
        self.app.processEvents()
        self.feedbacks = []
        self.widget.task_feedback.connect(self.feedbacks.append)

    def tearDown(self):
        if self.widget._worker and self.widget._worker.isRunning():
            self.widget._worker.wait(1000)
        self.widget.close()
        self.app.processEvents()

    def test_initialization_defaults(self):
        self.assertFalse(self.widget._busy)
        self.assertEqual(self.widget.input.toPlainText(), "")
        self.assertEqual(self.widget.result.toPlainText(), "")
        self.assertFalse(self.widget.pii_banner.isVisible())
        self.assertEqual(self.widget.mode_combo.count(), 2)
        self.assertEqual(self.widget.mode_combo.currentData(), "offline_direct")
        self.assertTrue(self.widget.submit_btn.isEnabled())

    def test_chip_click_populates_input(self):
        chips_buttons = self.widget.findChildren(QPushButton)
        # Find first chip button matching CHIPS[0][0]
        label0, template0 = CHIPS[0]
        btn = next((b for b in chips_buttons if b.text() == label0), None)
        self.assertIsNotNone(btn)
        btn.click()
        self.assertEqual(self.widget.input.toPlainText(), template0)

    def test_chip_click_ignored_when_busy(self):
        self.widget._set_busy(True)
        self.widget.input.setPlainText("이전 내용")
        label0, template0 = CHIPS[0]
        btn = next((b for b in self.widget.findChildren(QPushButton) if b.text() == label0), None)
        self.assertIsNotNone(btn)
        btn.click()
        self.assertEqual(self.widget.input.toPlainText(), "이전 내용")

    def test_pii_live_detection_and_mask_action(self):
        self.widget.input.setPlainText("홍길동 선생님 010-1234-5678 로 연락 주세요.")
        self.app.processEvents()
        self.assertTrue(self.widget.pii_banner.isVisible())
        self.assertIn("연락처", self.widget.pii_banner.text())

        # Click mask PII
        self.widget.mask_pii_btn.click()
        self.app.processEvents()
        self.assertFalse(self.widget.pii_banner.isVisible())
        self.assertNotIn("010-1234-5678", self.widget.input.toPlainText())
        self.assertIn("[연락처 가림]", self.widget.input.toPlainText())
        self.assertIn("success", self.feedbacks)

    def test_load_from_file_finder(self):
        self.widget.load_from_file_finder("2026_운영계획.hwpx", "1. 목적\n2. 방침")
        text = self.widget.input.toPlainText()
        self.assertIn("2026_운영계획.hwpx", text)
        self.assertIn("1. 목적\n2. 방침", text)

    def test_load_document_alias(self):
        self.widget.load_document("회의록.docx", "안건 1번 통과")
        text = self.widget.input.toPlainText()
        self.assertIn("회의록.docx", text)
        self.assertIn("안건 1번 통과", text)

    def test_ai_worker_calls_optimize_ai_with_expected_args(self):
        from syeonpet.chat.direct_work_ui import _AIWorker, ASSISTANT_SYSTEM
        worker = _AIWorker("테스트 요청")
        received = []
        worker.finished_signal.connect(lambda res, err: received.append((res, err)))
        with patch("optimizer_ai.optimize_ai", return_value={"result": "성공적인 결과"}) as mock_opt:
            worker.run()
            mock_opt.assert_called_once_with(
                "테스트 요청",
                provider="jarvisclaw",
                task="writing",
                detail="balanced",
                system_prompt=ASSISTANT_SYSTEM,
            )
            self.assertEqual(received, [("성공적인 결과", "")])

    def test_set_font_scale(self):
        self.widget.set_font_scale(15.0)
        self.assertEqual(self.widget.input.font().pointSizeF(), 15.0)
        self.assertEqual(self.widget.result.font().pointSizeF(), 15.0)

    def test_submit_empty_shows_info_dialog(self):
        with patch.object(QMessageBox, "information") as mock_info:
            self.widget.input.setPlainText("   ")
            self.widget.submit()
            mock_info.assert_called_once()
            self.assertFalse(self.widget._busy)

    def test_submit_offline_direct(self):
        self.widget.input.setPlainText("학부모 총회 안내 문자 작성해 줘")
        self.widget.mode_combo.setCurrentIndex(0)  # offline_direct
        self.widget.submit()
        self.assertIn("안내 문자 초안", self.widget.result.toPlainText())
        self.assertIn("학부모님", self.widget.result.toPlainText())
        self.assertIn("success", self.feedbacks)
        self.assertFalse(self.widget._busy)

    def test_submit_ai_direct_cancelled_by_user(self):
        self.widget.input.setPlainText("공문 기안 작성해 줘")
        self.widget.mode_combo.setCurrentIndex(1)  # ai_direct
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.No):
            self.widget.submit()
            self.assertFalse(self.widget._busy)
            self.assertEqual(self.widget.result.toPlainText(), "")

    def test_submit_ai_direct_success(self):
        self.widget.input.setPlainText("공문 기안 작성해 줘")
        self.widget.mode_combo.setCurrentIndex(1)  # ai_direct
        busy_states = []
        self.widget.busy_changed.connect(busy_states.append)

        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Yes):
            with patch("optimizer_ai.optimize_ai", return_value="[AI 작성 완료 공문 초안]"):
                self.widget.submit()
                self.assertTrue(busy_states[0])
                if self.widget._worker:
                    self.widget._worker.wait(2000)
                self.app.processEvents()

        self.assertIn("[AI 작성 완료 공문 초안]", self.widget.result.toPlainText())
        self.assertFalse(self.widget._busy)
        self.assertIn("success", self.feedbacks)

    def test_submit_ai_direct_failure_falls_back_to_offline(self):
        self.widget.input.setPlainText("공문 기안 작성해 줘")
        self.widget.mode_combo.setCurrentIndex(1)  # ai_direct

        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Yes):
            with patch("optimizer_ai.optimize_ai", side_effect=RuntimeError("AI Server Offline")):
                with patch.object(QMessageBox, "information") as mock_info:
                    self.widget.submit()
                    if self.widget._worker:
                        self.widget._worker.wait(2000)
                    self.app.processEvents()
                    mock_info.assert_called_once()

        self.assertFalse(self.widget._busy)
        self.assertTrue(len(self.widget.result.toPlainText()) > 0)
        self.assertIn("success", self.feedbacks)

    def test_copy_result(self):
        self.widget.result.setPlainText("복사할 내용입니다.")
        self.widget._copy_result()
        app = QApplication.instance()
        self.assertEqual(app.clipboard().text(), "복사할 내용입니다.")
        self.assertIn("copied", self.feedbacks)

    def test_save_to_document(self):
        self.widget.result.setPlainText("저장할 초안 내용입니다.")
        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=("test.txt", "텍스트 (*.txt)")):
            with patch("syeonpet.work.doc_export.export_to_txt") as mock_export:
                with patch.object(QMessageBox, "information"):
                    self.widget._save_to_document()
                    mock_export.assert_called_once_with("저장할 초안 내용입니다.", "test.txt")
                    self.assertIn("success", self.feedbacks)

    def test_open_web_ai(self):
        self.widget.result.setPlainText("뤼튼에 붙여넣을 내용")
        with patch("syeonpet.chat.direct_work_ui.copy_and_open_web_ai") as mock_open:
            with patch.object(QMessageBox, "information"):
                self.widget._open_web_ai()
                mock_open.assert_called_once_with("뤼튼에 붙여넣을 내용", "wrtn")
                self.assertIn("copied", self.feedbacks)


if __name__ == "__main__":
    unittest.main()
