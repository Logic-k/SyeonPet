"""Prompt-only window, readable opacity, and preservation on close/quit."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
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

    def test_small_window_error_notice_is_visible_without_losing_focus_or_draft(self):
        self.panel.resize(360, 450)
        self.panel.show()
        prompt = self.panel.prompt_conversation
        prompt.history_toggle.click()
        self.app.processEvents()
        prompt.input.setFocus()
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
