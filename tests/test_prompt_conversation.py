"""Exercise the in-pet conversation without a network or user data."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QApplication

from optimizer_chat import PromptConversation
from optimizer_library import LibraryDialog, LibraryStore


class PromptConversationTests(unittest.TestCase):
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
        while self.panel._busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        self.assertFalse(self.panel._busy, "worker did not complete")

    def _submit_offline(self, text):
        self.panel.input.setPlainText(text)
        self.panel.submit()
        self._wait_idle()

    def test_compact_history_toggle_preserves_the_conversation_and_draft(self):
        self._submit_offline("회의 메모를 정리해 주세요")
        self.panel.input.setPlainText("추가할 조건")
        history = self.panel.conversation.toPlainText()
        self.assertFalse(self.panel.conversation.isVisible())
        self.panel.history_toggle.setFocus()
        QApplication.sendEvent(self.panel.history_toggle, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
        QApplication.sendEvent(self.panel.history_toggle, QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
        self.assertTrue(self.panel.conversation.isVisible())
        self.assertEqual(self.panel.conversation.toPlainText(), history)
        self.assertEqual(self.panel.input.toPlainText(), "추가할 조건")
        self.panel.history_toggle.click()
        self.assertFalse(self.panel.conversation.isVisible())
        self.assertEqual(self.panel.conversation.toPlainText(), history)

    def test_busy_signal_emits_only_transitions_and_finishes_on_failure(self):
        transitions = []
        self.panel.busy_changed.connect(transitions.append)
        self.panel._set_busy(False)
        self.panel._set_busy(True)
        self.panel._set_busy(True)
        self.panel._pending = ("원문", "원문", "", "vireonix")
        self.panel._fail("연결 실패")
        self.assertEqual(transitions, [True, False])
        self.assertFalse(self.panel.is_busy)

    def test_thread_start_failure_resets_busy_and_preserves_input(self):
        transitions = []
        self.panel.busy_changed.connect(transitions.append)
        self.panel.input.setPlainText("보존할 요청")
        with patch("optimizer_chat.threading.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("thread creation failed")
            self.panel.submit()
        self.assertEqual(transitions, [True, False])
        self.assertTrue(self.panel.send.isEnabled())
        self.assertEqual(self.panel.input.toPlainText(), "보존할 요청")
        self.assertIn("시작하지 못했습니다", self.panel.status.text())

    def test_first_request_and_refinement_preserve_original_and_edited_result(self):
        self._submit_offline("회의록을 정리해 주세요. 3개 항목을 유지하세요.")
        first = self.panel.result.toPlainText()
        self.assertIn("회의록을 정리해 주세요. 3개 항목을 유지하세요.", first)
        self.assertIn("기본", self.panel.conversation.toPlainText())
        self.assertIn("AI", self.panel.status.text())
        self.panel.result.setPlainText(first + "\n사용자가 직접 추가한 조건")
        self._submit_offline("표 형식으로 작성해 주세요.")
        second = self.panel.result.toPlainText()
        self.assertEqual(self.panel.original, "회의록을 정리해 주세요. 3개 항목을 유지하세요.")
        self.assertEqual(self.panel.revisions, ["표 형식으로 작성해 주세요."])
        self.assertIn(self.panel.original, second)
        self.assertIn("표 형식으로 작성해 주세요.", second)
        self.assertIn("사용자가 직접 추가한 조건", second)
        self.assertEqual(self.panel.result_choice.count(), 2)
        self.panel.result_choice.setCurrentIndex(0)
        self.assertTrue(self.panel.result.toPlainText().endswith("사용자가 직접 추가한 조건"))
        self.panel.result_choice.setCurrentIndex(1)
        self.assertEqual(self.panel.result.toPlainText(), second)

    def test_cancelling_external_confirmation_keeps_input_and_never_calls_provider(self):
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("전송 전 유지할 원문")
        with patch.object(self.panel, "_confirm") as confirm, patch("optimizer_chat.optimize_ai") as ai:
            self.panel.submit()
            self.assertEqual(confirm.call_count, 1)
            ai.assert_not_called()
            self.assertFalse(self.panel._busy)
            self.assertEqual(self.panel.input.toPlainText(), "전송 전 유지할 원문")
            self.assertEqual(self.panel.original, "")

    def test_ai_quantity_change_is_visible_in_conversation_review(self):
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("광합성을 세 문장으로 설명해 주세요.")
        reply = {"result": "광합성을 세 문장 이내로 설명해 주세요.", "notes": [], "mode": "ai"}
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", return_value=reply):
            self.panel.submit()
            self._wait_idle()
        self.assertIn("수량 범위 확인 필요", self.panel.status.text())
        self.assertEqual(self.panel.result.toPlainText(), reply["result"])

    def test_refining_an_older_edited_result_preserves_later_conditions(self):
        self._submit_offline("원래 요청")
        self.panel.result.setPlainText(self.panel.result.toPlainText() + "\n직접 수정한 결과")
        self._submit_offline("첫 추가 조건")
        self._submit_offline("두 번째 추가 조건")
        self.panel.result_choice.setCurrentIndex(0)
        self.assertIn("직접 수정한 결과", self.panel.result.toPlainText())
        self._submit_offline("세 번째 추가 조건")
        result = self.panel.result.toPlainText()
        for content in ("직접 수정한 결과", "첫 추가 조건", "두 번째 추가 조건", "세 번째 추가 조건"):
            self.assertEqual(result.count(content), 1, content)
        self.assertEqual(self.panel.revisions, ["첫 추가 조건", "두 번째 추가 조건", "세 번째 추가 조건"])

    def test_failed_external_request_preserves_draft_and_previous_result(self):
        self._submit_offline("원래 요청을 정리해 주세요")
        self.panel.result.setPlainText("직접 수정한 기존 결과")
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("추가 조건도 유지")
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", side_effect=RuntimeError("연결 실패")) as ai:
            self.panel.submit()
            self._wait_idle()
            ai.assert_called_once()
        self.assertEqual(self.panel.input.toPlainText(), "추가 조건도 유지")
        self.assertEqual(self.panel.original, "원래 요청을 정리해 주세요")
        self.assertEqual(self.panel.result.toPlainText(), "직접 수정한 기존 결과")
        self.assertEqual(self.panel.revisions, [])
        self.assertIn("연결 실패", self.panel.status.text())
        self.assertEqual(self.panel.provider.currentData(), "vireonix")
        self.assertIn("션에게 보내기", self.panel.status.text())
        self.assertIn("직접 선택", self.panel.status.text())
        self.assertIn("기본 정리는 AI 재작성이 아닙니다", self.panel.status.text())

    def test_switching_service_after_error_requires_new_consent(self):
        self._submit_offline("원래 요청")
        self.panel.result.setPlainText("이전 결과")
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("추가 조건")
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", side_effect=RuntimeError("서비스 연결 실패")) as ai:
            self.panel.submit()
            self._wait_idle()
            self.assertEqual(ai.call_count, 1)
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("pollinations-classic"))
        with patch.object(self.panel, "_confirm") as confirm, patch("optimizer_chat.optimize_ai") as ai:
            self.panel.submit()
            self.assertEqual(confirm.call_count, 1)
            self.assertIn("Pollinations Classic", confirm.call_args.args[1])
            ai.assert_not_called()
        self.assertEqual(self.panel.input.toPlainText(), "추가 조건")
        self.assertEqual(self.panel.result.toPlainText(), "이전 결과")
        self.assertEqual(self.panel.provider.currentData(), "pollinations-classic")

    def test_failure_while_asleep_keeps_send_disabled(self):
        self.panel._pending = ("원문", "원문", "", "vireonix")
        self.panel.input.setPlainText("원문")
        self.panel._set_busy(True)
        self.panel.set_sleep_paused(True)
        self.panel._fail("서비스 오류")
        self.assertFalse(self.panel.send.isEnabled())
        self.assertEqual(self.panel.input.toPlainText(), "원문")
        self.assertIn("션에게 보내기", self.panel.status.text())
        self.assertIn("션을 깨운 뒤", self.panel.status.text())

    def test_ai_receives_explicitly_confirmed_previous_edit_and_revision(self):
        self._submit_offline("원래 요청")
        self.panel.result.setPlainText("내가 수정한 결과")
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("pollinations-classic"))
        self.panel.input.setPlainText("간단한 표로")
        reply = {"result": "새 결과", "notes": []}
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", return_value=reply) as ai:
            self.panel.submit()
            self._wait_idle()
            self.assertEqual(ai.call_args.kwargs["previous_result"], "내가 수정한 결과")
            self.assertEqual(ai.call_args.kwargs["revision"], "간단한 표로")
            self.assertEqual(ai.call_args.kwargs["provider"], "pollinations-classic")
        self.assertEqual(self.panel.result.toPlainText(), "새 결과")
        self.assertEqual(self.panel.input.toPlainText(), "")

    def test_template_inserts_into_current_input_without_discarding_it(self):
        self.panel.input.setPlainText("기존 요청 ")
        cursor = self.panel.input.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.panel.input.setTextCursor(cursor)
        with patch("optimizer_templates.show_template_picker") as picker:
            self.panel.template.click()
            callback = picker.call_args.args[1]
            callback("템플릿 본문")
        self.assertEqual(self.panel.input.toPlainText(), "기존 요청 템플릿 본문")

    def test_modeless_template_selection_during_request_preserves_changed_input(self):
        from optimizer_core import create_offline_prompt

        release = threading.Event()
        started = threading.Event()

        def delayed(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("test worker timed out")
            return create_offline_prompt(*args, **kwargs)

        self.panel.template.click()
        picker = self.panel._template_dialog
        picker.list.setCurrentRow(0)
        picker.preview.setPlainText("사용자가 고른 템플릿")
        self.panel.input.setPlainText("원래 요청")
        with patch("optimizer_chat.create_offline_prompt", side_effect=delayed):
            try:
                self.panel.submit()
                self.assertTrue(started.wait(2))
                self.assertTrue(self.panel._busy)
                picker.select_button.click()
                changed = self.panel.input.toPlainText()
                self.assertIn("사용자가 고른 템플릿", changed)
            finally:
                release.set()
            self._wait_idle()
        self.assertEqual(self.panel.original, "원래 요청")
        self.assertEqual(self.panel.input.toPlainText(), changed)
        self.assertIn("그대로 남겨", self.panel.status.text())

    def test_changed_input_after_external_confirmation_is_not_transmitted(self):
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("확인한 원문")
        with patch.object(self.panel, "_confirm") as confirm, patch("optimizer_chat.optimize_ai") as ai:
            self.panel.submit()
            callback = confirm.call_args.args[2]
            self.panel.input.setPlainText("확인 후 달라진 원문")
            callback()
            self.app.processEvents()
            ai.assert_not_called()
        self.assertEqual(self.panel.input.toPlainText(), "확인 후 달라진 원문")
        self.assertFalse(self.panel._busy)
        self.assertIn("전송하지 않았어요", self.panel.status.text())

    def test_new_request_confirmation_does_not_clear_later_edits(self):
        self.panel.input.setPlainText("처음 입력")
        with patch.object(self.panel, "_confirm") as confirm:
            self.panel.reset.click()
            callback = confirm.call_args.args[2]
            self.panel.input.setPlainText("확인 중 추가한 내용")
            callback()
        self.assertEqual(self.panel.input.toPlainText(), "확인 중 추가한 내용")
        self.assertIn("시작하지 않았어요", self.panel.status.text())

    def test_library_restore_asks_again_if_draft_changes_while_open(self):
        self.panel.input.setPlainText("기존 입력")
        with tempfile.TemporaryDirectory(prefix="syeon-restore-race-") as temp:
            store = LibraryStore(Path(temp) / "library.json")
            with patch("optimizer_library.LibraryStore", return_value=store):
                self.panel._open_library()
                self.panel.input.setPlainText("선택창에서 추가한 입력")
                with patch.object(self.panel, "_confirm") as confirm:
                    self.panel._restore_library({"prompt": "보관 원문", "result": "보관 결과"})
                    self.assertEqual(confirm.call_count, 1)
                    callback = confirm.call_args.args[2]
                    self.panel.input.setPlainText("다시 바뀐 입력")
                    callback()
                self.assertEqual(self.panel.input.toPlainText(), "다시 바뀐 입력")
                self.assertEqual(self.panel.original, "")
                self.panel._library_dialog.close()

    def test_new_request_requires_confirmation_and_cancel_keeps_everything(self):
        self._submit_offline("원래 요청")
        self.panel.result.setPlainText("수정 중인 결과")
        self.panel.input.setPlainText("작성 중인 추가 조건")
        with patch.object(self.panel, "_confirm") as confirm:
            self.panel.reset.click()
            self.assertEqual(confirm.call_count, 1)
        self.assertEqual(self.panel.original, "원래 요청")
        self.assertEqual(self.panel.input.toPlainText(), "작성 중인 추가 조건")
        self.assertEqual(self.panel.result.toPlainText(), "수정 중인 결과")

    def test_older_result_backup_import_restore_and_refinement_keep_revisions(self):
        self._submit_offline("원래 요청")
        self._submit_offline("첫 번째 추가 조건")
        self._submit_offline("두 번째 추가 조건")
        self.panel.result_choice.setCurrentIndex(0)
        self.panel.result.setPlainText(self.panel.result.toPlainText() + "\n이전 결과 직접 편집")
        visible_result = self.panel.result.toPlainText()
        with tempfile.TemporaryDirectory(prefix="syeon-library-test-") as temp:
            root = Path(temp)
            backup = root / "backup.json"
            first_store = LibraryStore(root / "first.json")
            second_store = LibraryStore(root / "second.json")
            with patch("optimizer_library.LibraryStore", return_value=first_store), \
                    patch.object(LibraryDialog, "_notify"):
                self.panel._open_library()
                original_dialog = self.panel._library_dialog
                self.assertEqual(self.panel.result.toPlainText(), visible_result)
                original_dialog._save()
                with patch.object(original_dialog, "_file_dialog", side_effect=lambda _title, _save, handler: handler(str(backup))):
                    original_dialog._export()
                self.assertTrue(backup.exists())
                self.assertEqual(first_store.load()[0]["syeonRevisions"], ["첫 번째 추가 조건", "두 번째 추가 조건"])
                original_dialog.close()
            with patch("optimizer_library.LibraryStore", return_value=second_store), \
                    patch.object(LibraryDialog, "_notify"):
                self.panel._open_library()
                dialog = self.panel._library_dialog
                with patch.object(dialog, "_file_dialog", side_effect=lambda _title, _save, handler: handler(str(backup))):
                    dialog._import()
                self.assertEqual(second_store.load()[0]["syeonRevisions"], ["첫 번째 추가 조건", "두 번째 추가 조건"])
                dialog.entries.setCurrentRow(0)
                dialog._restore()
                from PySide6.QtWidgets import QMessageBox
                dialog._dialogs[-1].done(int(QMessageBox.StandardButton.Yes))
                self.app.processEvents()
            self.assertEqual(self.panel.original, "원래 요청")
            self.assertEqual(self.panel.revisions, ["첫 번째 추가 조건", "두 번째 추가 조건"])
            self._submit_offline("세 번째 추가 조건")
            self.assertEqual(self.panel.revisions, ["첫 번째 추가 조건", "두 번째 추가 조건", "세 번째 추가 조건"])
            self.assertIn("이전 결과 직접 편집", self.panel.result.toPlainText())
            self.assertIn("첫 번째 추가 조건", self.panel.result.toPlainText())
            self.assertIn("두 번째 추가 조건", self.panel.result.toPlainText())
            self.assertIn("세 번째 추가 조건", self.panel.result.toPlainText())


if __name__ == "__main__":
    unittest.main()
