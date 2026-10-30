"""Prompt-only window, readable opacity, and preservation on close/quit."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSettings
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from behavior import BehaviorController
from chat import ChatPanel
from pet import PetWidget, SpriteBank


class ChatWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.sprites = SpriteBank()

    def setUp(self):
        self.original_palette = self.app.palette()
        palette = QPalette(self.original_palette)
        palette.setColor(QPalette.ColorRole.Text, QColor("white"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#1e1e1e"))
        self.app.setPalette(palette)
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / "test.ini"), QSettings.Format.IniFormat)
        self.pet = PetWidget(self.sprites)
        self.pet.tick.stop()
        self.behavior = BehaviorController(self.pet)
        self.behavior.timer.stop()
        self.panel = ChatPanel(self.pet, self.behavior, {}, settings=self.settings)

    def tearDown(self):
        if getattr(self.panel, "_quit_dialog", None):
            self.panel._quit_dialog.reject()
        self.panel.close()
        self.pet.close()
        self.settings.sync()
        self.app.setPalette(self.original_palette)
        self.app.processEvents()
        self.temp.cleanup()

    def test_window_is_prompt_only_and_closed_draft_reopens(self):
        prompt = self.panel.prompt_conversation
        prompt.input.setPlainText("요청 초안 <b>그대로</b>")
        prompt.result.setPlainText("수정한 결과")
        self.panel.show_prompt_near_pet()
        self.assertFalse(hasattr(self.panel, "tabs"))
        self.assertFalse(hasattr(self.panel, "brain"))
        self.panel.close()
        self.panel.show_near_pet()
        self.assertEqual(prompt.input.toPlainText(), "요청 초안 <b>그대로</b>")
        self.assertEqual(prompt.result.toPlainText(), "수정한 결과")

    def test_update_quit_confirmation_exports_without_losing_draft(self):
        self.panel.show_near_pet()
        self.panel.prompt_conversation.input.setPlainText("재시작할 때 보존할 내용")
        exported = []
        self.panel.request_app_quit(lambda: exported.append(self.panel.export_update_state()),
                                    preserve_for_update=True)
        dialog = self.panel._quit_dialog
        self.assertIn("다음 실행에서 복원", dialog.text())
        dialog.done(QMessageBox.StandardButton.Yes)
        self.app.processEvents()
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["conversation"]["input"], "재시작할 때 보존할 내용")

    def test_update_quit_waits_for_processing(self):
        prompt = self.panel.prompt_conversation
        prompt.input.setPlainText("유지할 내용")
        prompt._set_busy(True)
        quit_calls = []
        self.panel.request_app_quit(lambda: quit_calls.append(True), preserve_for_update=True)
        self.assertEqual(quit_calls, [])
        self.assertIn("정리가 끝난 뒤", prompt.inline_notice.text())
        self.assertEqual(prompt.input.toPlainText(), "유지할 내용")
        prompt._set_busy(False)

    def test_update_restore_into_initially_hidden_window_preserves_template(self):
        self.panel.show_near_pet()
        prompt = self.panel.prompt_conversation
        prompt.input.setPlainText("보존할 입력")
        prompt._insert_template()
        picker = prompt._template_dialog
        picker.list.setCurrentRow(0)
        picker.preview.setPlainText("작성 중인 템플릿")
        state = self.panel.export_update_state()
        picker.close()
        self.panel.close()
        self.panel = ChatPanel(self.pet, self.behavior, {}, settings=self.settings)
        self.assertFalse(self.panel.isVisible())
        self.assertTrue(self.panel.restore_update_state(state))
        self.panel.show_near_pet()
        self.app.processEvents()
        restored = self.panel.prompt_conversation._template_dialog
        self.assertTrue(restored.isVisible())
        self.assertEqual(restored.preview.toPlainText(), "작성 중인 템플릿")
        self.assertEqual(self.panel.prompt_conversation.input.toPlainText(), "보존할 입력")
        restored.close()

    def test_window_fits_small_negative_screen_and_restores_minimum_on_larger_screen(self):
        prompt = self.panel.prompt_conversation
        prompt.input.setPlainText("보존할 입력")
        prompt.result.setPlainText("보존할 결과")
        small = QRect(-550, 30, 400, 400)
        large = QRect(0, 0, 1400, 900)
        selected = {"bounds": small, "pet": QRect(-510, 70, 65, 65)}
        screen = SimpleNamespace(availableGeometry=lambda: selected["bounds"])
        with patch.object(self.panel, "_screen_for_pet", return_value=screen), \
             patch.object(self.pet, "sprite_rect_global", side_effect=lambda: selected["pet"]):
            self.panel.show_near_pet()
            self.app.processEvents()
            self.app.processEvents()
            frame = self.panel.frameGeometry()
            self.assertTrue(small.contains(frame), f"frame={frame}, bounds={small}")
            self.assertLessEqual(self.panel.minimumHeight(), small.height())
            self.assertGreater(self.panel.conversation_scroll.verticalScrollBar().maximum(), 0)
            self.assertEqual(prompt.input.toPlainText(), "보존할 입력")
            self.assertEqual(prompt.result.toPlainText(), "보존할 결과")

            self.panel.close()
            selected.update(bounds=large, pet=QRect(200, 200, 65, 65))
            self.panel.show_near_pet()
            self.app.processEvents()
            self.app.processEvents()
            self.assertTrue(large.contains(self.panel.frameGeometry()))
            self.assertEqual(self.panel.minimumWidth(), 360)
            self.assertEqual(self.panel.minimumHeight(), 450)
            self.assertEqual(prompt.input.toPlainText(), "보존할 입력")

    def test_small_window_error_notice_is_visible_without_losing_focus_or_draft(self):
        self.panel.resize(360, 450)
        self.panel.show()
        prompt = self.panel.prompt_conversation
        prompt.history_toggle.click()
        self.app.processEvents()
        prompt.input.setFocus()
        prompt.provider.setCurrentIndex(prompt.provider.findData("offline"))
        prompt.submit()
        self.app.processEvents()
        notice = prompt.inline_notice
        viewport = self.panel.conversation_scroll.viewport()
        notice_top = notice.mapTo(viewport, notice.rect().topLeft()).y()
        notice_bottom = notice.mapTo(viewport, notice.rect().bottomLeft()).y()
        self.assertTrue(notice.isVisible())
        self.assertGreaterEqual(notice_top, 0)
        self.assertLess(notice_bottom, viewport.height())
        self.assertTrue(prompt.input.hasFocus())
        self.assertIn("입력", notice.text())
        prompt.input.setPlainText("유지할 원문")
        prompt.result.setPlainText("유지할 결과")
        prompt._pending = ("유지할 원문", "유지할 원문", "", "vireonix")
        prompt._fail("인터넷 연결을 확인해 주세요.")
        self.app.processEvents()
        notice_top = notice.mapTo(viewport, notice.rect().topLeft()).y()
        notice_bottom = notice.mapTo(viewport, notice.rect().bottomLeft()).y()
        self.assertGreaterEqual(notice_top, 0)
        self.assertLess(notice_bottom, viewport.height())
        self.assertIn("인터넷 연결", notice.text())
        self.assertEqual(prompt.input.toPlainText(), "유지할 원문")
        self.assertEqual(prompt.result.toPlainText(), "유지할 결과")
        self.assertTrue(prompt.input.hasFocus())

    def test_small_window_shows_result_on_completion_with_history_expanded(self):
        self.panel.resize(360, 450)
        self.panel.show()
        prompt = self.panel.prompt_conversation
        prompt.history_toggle.click()
        prompt.input.setPlainText("회의 내용을 정리해 주세요")
        completed = []
        prompt.result_ready.connect(lambda: completed.append(True))
        self.app.processEvents()
        prompt.provider.setCurrentIndex(prompt.provider.findData("offline"))
        prompt.submit()
        deadline = time.monotonic() + 5
        while prompt.is_busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        # Completion scroll is queued after Qt finishes the focus/layout pass.
        viewport = self.panel.conversation_scroll.viewport()
        scroll_deadline = time.monotonic() + 1
        while (prompt.result.mapTo(viewport, prompt.result.rect().topLeft()).y() + 60 >= viewport.height()
               and time.monotonic() < scroll_deadline):
            self.app.processEvents()
            time.sleep(.005)
        self.assertFalse(prompt.is_busy)
        self.assertEqual(completed, [True])
        self.assertIn("회의 내용을 정리해 주세요", prompt.result.toPlainText())
        result_top = prompt.result.mapTo(viewport, prompt.result.rect().topLeft()).y()
        result_bottom = prompt.result.mapTo(viewport, prompt.result.rect().bottomLeft()).y()
        choice_top = prompt.result_choice.mapTo(viewport, prompt.result_choice.rect().topLeft()).y()
        self.assertGreaterEqual(result_top, 0)
        self.assertLess(result_top + 60, viewport.height(),
                        f"top={result_top}, bottom={result_bottom}, viewport={viewport.height()}, "
                        f"scroll={self.panel.conversation_scroll.verticalScrollBar().value()}/"
                        f"{self.panel.conversation_scroll.verticalScrollBar().maximum()}")
        self.assertGreaterEqual(choice_top, 0, "result heading and selector must be visible")
        focused = QApplication.focusWidget()
        self.assertIn(focused, (prompt.input, prompt.result))
        focus_top = focused.mapTo(viewport, focused.rect().topLeft()).y()
        focus_bottom = focused.mapTo(viewport, focused.rect().bottomLeft()).y()
        self.assertGreaterEqual(focus_top, 0)
        self.assertLess(focus_bottom, viewport.height())

    def test_opacity_is_bounded_and_persisted_separately(self):
        self.panel.opacity_slider.setValue(70)
        self.assertEqual(self.panel.opacity_value.text(), "70%")
        self.assertAlmostEqual(self.panel.windowOpacity(), .7, delta=.01)
        self.assertEqual(int(self.settings.value("opacityPercent")), 70)
        other = ChatPanel(self.pet, self.behavior, {}, settings=self.settings)
        self.assertEqual(other.opacity_slider.value(), 70)
        other.opacity_slider.setValue(0)
        self.assertEqual(other.opacity_slider.value(), 60)
        other.close()

    def test_quit_is_guarded_and_cancel_keeps_draft(self):
        called = []
        self.panel.prompt_conversation.input.setPlainText("보관할 초안")
        self.panel.request_app_quit(lambda: called.append(True))
        dialog = self.panel._quit_dialog
        self.assertIsNotNone(dialog)
        dialog.button(QMessageBox.StandardButton.No).click()
        self.assertFalse(called)
        self.assertEqual(self.panel.prompt_conversation.input.toPlainText(), "보관할 초안")

    def test_quit_refuses_changed_state_and_empty_quit_is_immediate(self):
        called = []
        prompt = self.panel.prompt_conversation
        prompt.input.setPlainText("초안")
        self.panel.request_app_quit(lambda: called.append(True))
        dialog = self.panel._quit_dialog
        prompt.input.setPlainText("확인 중 새 초안")
        dialog.button(QMessageBox.StandardButton.Yes).click()
        self.assertFalse(called)
        self.assertIn("다시 확인", prompt.status.text())
        prompt.input.clear()
        self.panel.request_app_quit(lambda: called.append(True))
        self.assertEqual(called, [True])

    def test_quit_confirms_unchanged_draft(self):
        called = []
        self.panel.prompt_conversation.input.setPlainText("초안")
        self.panel.request_app_quit(lambda: called.append(True))
        self.panel._quit_dialog.button(QMessageBox.StandardButton.Yes).click()
        self.assertEqual(called, [True])

    def test_open_template_draft_is_protected_and_picker_is_reused(self):
        prompt = self.panel.prompt_conversation
        prompt._insert_template()
        picker = prompt._template_dialog
        picker.list.setCurrentRow(0)
        picker.preview.setPlainText("템플릿에서 수정한 초안")
        prompt._insert_template()
        self.assertIs(prompt._template_dialog, picker)
        self.assertTrue(self.panel.has_pending_work())
        called = []
        self.panel.request_app_quit(lambda: called.append(True))
        dialog = self.panel._quit_dialog
        picker.preview.setPlainText("확인 중 다시 수정한 초안")
        dialog.button(QMessageBox.StandardButton.Yes).click()
        self.assertFalse(called)
        self.assertEqual(picker.preview.toPlainText(), "확인 중 다시 수정한 초안")
        self.assertIn("다시 확인", prompt.status.text())
        picker.reject()


if __name__ == "__main__":
    unittest.main()
