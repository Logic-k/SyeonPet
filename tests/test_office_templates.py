"""Education-office catalog and guided picker integration."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import optimizer_templates as templates_module
from optimizer_templates import filter_templates, load_templates, show_template_picker


class OfficeTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.assets = Path(__file__).resolve().parents[1] / "assets"

    def test_original_catalog_is_unchanged_and_office_templates_are_actionable(self):
        original = json.loads((self.assets / "prompt_templates.json").read_text(encoding="utf-8"))
        office = json.loads((self.assets / "seobu_office_templates.json").read_text(encoding="utf-8"))
        combined = load_templates()
        self.assertEqual(combined, original + office)
        self.assertEqual(len(original), 49)
        self.assertGreaterEqual(len(office), 12)
        self.assertEqual(len({item["id"] for item in combined}), len(combined))
        for item in office:
            with self.subTest(item=item["id"]):
                self.assertTrue(item["id"].startswith("seobu-"))
                self.assertTrue(item["category"].startswith("서부교육지원청·"))
                self.assertIn("[", item["text"])
                self.assertIn("]", item["text"])
                self.assertTrue(item["label"] and item["description"])

    def test_office_only_filter_and_picker_preserve_existing_draft(self):
        combined = load_templates()
        office = filter_templates(combined, office_only=True)
        self.assertGreaterEqual(len(office), 12)
        self.assertTrue(all(item["id"].startswith("seobu-") for item in office))
        chosen = []
        dialog = show_template_picker(on_selected=chosen.append)
        dialog.list.setCurrentRow(0)
        dialog.preview.setPlainText("이전에 작성한 초안")
        dialog.office_button.click()
        self.assertEqual(dialog.list.count(), len(office))
        dialog.list.setCurrentRow(0)
        self.assertIn("[", dialog.preview.toPlainText())
        dialog.office_button.click()
        dialog.list.setCurrentRow(0)
        self.assertEqual(dialog.preview.toPlainText(), "이전에 작성한 초안")
        self.assertEqual(chosen, [])
        dialog.cancel_button.click()
        self.assertEqual(chosen, [])

    def test_routine_summary_event_and_public_guideline_templates_are_searchable(self):
        catalog = load_templates()
        for query, expected in (("업무 취합", "seobu-weekly-monthly-summary"),
                                ("행사 운영", "seobu-event-checklist"),
                                ("지침 비교", "seobu-public-guideline-compare")):
            with self.subTest(query=query):
                matches = filter_templates(catalog, query, office_only=True)
                self.assertIn(expected, [item["id"] for item in matches])

    def test_office_data_unavailable_keeps_original_catalog_and_shows_warning(self):
        read = templates_module._read_catalog
        def read_or_fail(filename):
            if filename == "seobu_office_templates.json":
                raise OSError("test missing catalog")
            return read(filename)
        with patch.object(templates_module, "_read_catalog", side_effect=read_or_fail):
            dialog = show_template_picker()
            self.assertEqual(dialog.list.count(), 49)
            self.assertFalse(dialog.office_button.isEnabled())
            self.assertIn("test missing catalog", dialog.catalog_warning.text())
            self.assertFalse(dialog.catalog_warning.isHidden())
            dialog.cancel_button.click()


if __name__ == "__main__":
    unittest.main()
