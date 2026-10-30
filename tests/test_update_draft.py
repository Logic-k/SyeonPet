"""Restart snapshot tests use synthetic content and no network or user files."""
import json
import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt

from optimizer_chat import PromptConversation


class UpdateDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.source = PromptConversation()
        self.target = PromptConversation()
        self.source.show()
        self.target.show()
        self.app.processEvents()

    def tearDown(self):
        for panel in (self.source, self.target):
            for name in ("_confirmation", "_library_dialog", "_template_dialog"):
                dialog = getattr(panel, name, None)
                try:
                    if dialog is not None:
                        dialog.close()
                except RuntimeError:
                    pass
            panel.close()
        self.app.processEvents()

    def wait_for_result(self):
        deadline = time.monotonic() + 5
        while self.source.is_busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        self.assertFalse(self.source.is_busy)

    def test_round_trip_preserves_edited_history_and_never_sends(self):
        self.source.provider.setCurrentIndex(self.source.provider.findData("offline"))
        self.source.input.setPlainText("가상의 첫 요청")
        self.source.submit()
        self.wait_for_result()
        self.source.result.setPlainText("사용자가 편집한 첫 결과")
        self.source.input.setPlainText("추가 조건")
        self.source.submit()
        self.wait_for_result()
        self.source.result_choice.setCurrentIndex(0)
        self.source.result.setPlainText("아직 저장되지 않은 결과 수정")
        self.source.input.setPlainText("작성 중인 추가 조건")
        self.source.provider.setCurrentIndex(self.source.provider.findData("vireonix"))
        self.source.history_toggle.setChecked(True)
        with patch("optimizer_chat.optimize_ai") as send:
            state = json.loads(json.dumps(self.source.export_update_state(), ensure_ascii=False))
            self.target.restore_update_state(state)
            self.app.processEvents()
            send.assert_not_called()
        self.assertEqual(self.target.original, self.source.original)
        self.assertEqual(self.target.revisions, self.source.revisions)
        self.assertEqual(self.target.result.toPlainText(), "아직 저장되지 않은 결과 수정")
        self.assertEqual(self.target.input.toPlainText(), "작성 중인 추가 조건")
        self.assertEqual(self.target.result_choice.currentIndex(), 0)
        self.assertEqual(self.target.provider.currentData(), "vireonix")
        self.assertEqual(self.target.conversation.toPlainText(), self.source.conversation.toPlainText())
        self.assertTrue(self.target.history_toggle.isChecked())
        self.target.result_choice.setCurrentIndex(1)
        self.target.result_choice.setCurrentIndex(0)
        self.assertEqual(self.target.result.toPlainText(), "아직 저장되지 않은 결과 수정")

    def test_unslotted_result_and_input_survive(self):
        self.source.result.setPlainText("첫 정리 전 쓴 결과 초안")
        self.source.input.setPlainText("아직 보내지 않은 원문")
        self.target.restore_update_state(self.source.export_update_state())
        self.assertEqual(self.target.result.toPlainText(), "첫 정리 전 쓴 결과 초안")
        self.assertEqual(self.target.input.toPlainText(), "아직 보내지 않은 원문")
        self.assertEqual(self.target.result_choice.count(), 0)

    def test_template_multiple_drafts_filter_and_selection_survive(self):
        self.source._insert_template()
        picker = self.source._template_dialog
        picker.office_button.setChecked(True)
        picker.list.setCurrentRow(0)
        first_id = picker._current_id
        picker.preview.setPlainText("첫 템플릿의 변경 내용")
        picker.list.setCurrentRow(1)
        second_id = picker._current_id
        picker.preview.setPlainText("두 번째 템플릿의 변경 내용")
        state = self.source.export_update_state()
        self.target.restore_update_state(state)
        restored = self.target._template_dialog
        self.assertTrue(restored.isVisible())
        self.assertTrue(restored.office_button.isChecked())
        self.assertEqual(restored._current_id, second_id)
        self.assertEqual(restored.preview.toPlainText(), "두 번째 템플릿의 변경 내용")
        self.assertEqual(restored._drafts[first_id], "첫 템플릿의 변경 내용")
        for row in range(restored.list.count()):
            if restored.list.item(row).data(Qt.ItemDataRole.UserRole)["id"] == first_id:
                restored.list.setCurrentRow(row)
                break
        self.assertEqual(restored.preview.toPlainText(), "첫 템플릿의 변경 내용")

    def test_busy_and_open_confirmation_stop_export(self):
        self.source._set_busy(True)
        with self.assertRaises(RuntimeError):
            self.source.export_update_state()
        self.source._set_busy(False)
        self.source.input.setPlainText("가상 요청")
        self.source.submit()
        self.assertTrue(self.source._confirmation.isVisible())
        with self.assertRaises(RuntimeError):
            self.source.export_update_state()
        self.source._confirmation.done(QMessageBox.StandardButton.No)
        self.assertEqual(self.source.input.toPlainText(), "가상 요청")

    def test_open_library_dialog_stops_export(self):
        with patch("optimizer_library.LibraryStore.load", return_value=[]):
            self.source._open_library()
        self.assertTrue(self.source._library_dialog.isVisible())
        with self.assertRaises(RuntimeError):
            self.source.export_update_state()
        self.source._library_dialog.close()

    def test_long_unsent_text_is_not_truncated(self):
        text = "임시 원문\n" * 10000
        self.source.input.setPlainText(text)
        self.target.restore_update_state(self.source.export_update_state())
        self.assertEqual(self.target.input.toPlainText(), text)

    def test_invalid_snapshot_and_nonempty_target_do_not_overwrite(self):
        self.source.input.setPlainText("원래 입력")
        state = self.source.export_update_state()
        broken = dict(state, provider="unknown")
        with self.assertRaises(ValueError):
            self.target.restore_update_state(broken)
        self.assertEqual(self.target.input.toPlainText(), "")
        self.target.input.setPlainText("기존 사용자 입력")
        with self.assertRaises(RuntimeError):
            self.target.restore_update_state(state)
        self.assertEqual(self.target.input.toPlainText(), "기존 사용자 입력")

    def test_missing_template_does_not_restore_partial_content(self):
        self.source.input.setPlainText("유지할 입력")
        state = self.source.export_update_state()
        state["template"] = {"current_id": "missing-id", "preview": "초안", "drafts": {},
                             "search": "", "category": "", "office_only": False}
        with self.assertRaises(ValueError):
            self.target.restore_update_state(state)
        self.assertEqual(self.target.input.toPlainText(), "")


if __name__ == "__main__":
    unittest.main()
