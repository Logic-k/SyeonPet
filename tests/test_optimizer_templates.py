"""Offline template data and picker behavior."""
import json
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QScrollArea

from optimizer_templates import filter_templates, load_templates, show_template_picker


class TemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_imported_catalog_and_search(self):
        templates = load_templates()
        original = json.loads((Path(__file__).resolve().parents[1] / "assets" / "prompt_templates.json").read_text(encoding="utf-8"))
        self.assertEqual(len(original), 49)
        self.assertEqual(templates[:49], original)
        self.assertGreater(len(templates), 49)
        self.assertEqual(len({item["id"] for item in templates}), len(templates))
        first = templates[0]
        self.assertEqual(first["id"], "general-request")
        self.assertIn("[원하는 일]", first["text"])
        self.assertIn("모르는 내용은 지어내지 마세요", first["text"])
        matches = filter_templates(templates, "이메일", "글쓰기와 소통")
        self.assertTrue(any(item["id"] == "email-draft" for item in matches))
        self.assertEqual(filter_templates(templates, "이메일", "자료 조사"), [])

    def test_picker_preserves_edited_draft_and_only_sends_on_choice(self):
        chosen = []
        dialog = show_template_picker(on_selected=chosen.append)
        dialog.list.setCurrentRow(0)
        dialog.preview.setPlainText("내가 수정한 내용")
        dialog.list.setCurrentRow(1)
        dialog.list.setCurrentRow(0)
        self.assertEqual(dialog.preview.toPlainText(), "내가 수정한 내용")
        self.assertEqual(chosen, [])
        dialog.select_button.click()
        self.assertEqual(chosen, ["내가 수정한 내용"])

    def test_cancel_has_no_callback(self):
        chosen = []
        dialog = show_template_picker(on_selected=chosen.append)
        dialog.list.setCurrentRow(0)
        dialog.cancel_button.click()
        self.assertEqual(chosen, [])

    def test_small_picker_keeps_editing_and_confirmation_reachable(self):
        chosen = []
        dialog = show_template_picker(on_selected=chosen.append)
        dialog.resize(360, 450)
        dialog.list.setCurrentRow(0)
        self.app.processEvents()
        scroll = dialog.findChild(QScrollArea, "templateScroll")
        scroll.ensureWidgetVisible(dialog.preview, 0, 0)
        self.app.processEvents()
        preview_bottom = dialog.preview.mapTo(scroll.viewport(), QPoint(0, dialog.preview.height())).y()
        button_bottom = dialog.select_button.mapTo(dialog, QPoint(0, dialog.select_button.height())).y()
        # Qt's frame can extend a few pixels past the viewport edge.
        self.assertLessEqual(preview_bottom, scroll.viewport().height() + 4)
        self.assertLessEqual(button_bottom, dialog.height())
        dialog.preview.setPlainText("수정한 초안")
        dialog.select_button.click()
        self.assertEqual(chosen, ["수정한 초안"])

    def test_large_font_allows_horizontal_access_in_narrow_window(self):
        previous_font = self.app.font()
        try:
            self.app.setFont(QFont("Arial", 18))
            dialog = show_template_picker()
            dialog.resize(360, 450)
            self.app.processEvents()
            scroll = dialog.findChild(QScrollArea, "templateScroll")
            scroll.ensureWidgetVisible(dialog.category, 0, 0)
            self.app.processEvents()
            category_right = dialog.category.mapTo(
                scroll.viewport(), QPoint(dialog.category.width(), 0)).x()
            self.assertLessEqual(category_right, scroll.viewport().width() + 4)
            dialog.cancel_button.click()
        finally:
            self.app.setFont(previous_font)


if __name__ == "__main__":
    unittest.main()
