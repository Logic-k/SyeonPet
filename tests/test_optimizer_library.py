"""Library compatibility and data-preservation tests with isolated files."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QMessageBox

from optimizer_library import LibraryStore, merge_libraries, parse_backup, validate_library


def item(**changes):
    data = {"id": "one", "title": "제목", "prompt": "원문\r\n👩‍💻",
            "result": "결과", "task": "writing", "detail": "balanced",
            "createdAt": "2026-10-29T12:00:00.000Z"}
    data.update(changes)
    return data


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "library.json"
        self.store = LibraryStore(self.path)

    def test_toolkit_backup_round_trip_and_clean_unknown_fields(self):
        raw = {"version": 1, "items": [item(apiKey="do-not-store")]}
        parsed = parse_backup(json.dumps(raw, ensure_ascii=False).encode("utf-8"))
        self.assertNotIn("apiKey", parsed[0])
        self.store.merge(parsed)
        self.assertEqual(self.store.load(), parsed)
        self.assertEqual(json.loads(self.path.read_text("utf-8"))["version"], 1)
        self.assertEqual(self.store.load()[0]["prompt"], "원문\r\n👩‍💻")

    def test_duplicate_skips_and_conflicting_id_is_preserved(self):
        first = validate_library({"version": 1, "items": [item()]})
        self.store.merge(first)
        merged, count = self.store.merge(first)
        self.assertEqual((len(merged), count), (1, 0))
        merged, count = self.store.merge([item(prompt="다른 원문")])
        self.assertEqual((len(merged), count), (2, 1))
        self.assertNotEqual(merged[0]["id"], merged[1]["id"])
        self.assertEqual({x["prompt"] for x in merged}, {"원문\r\n👩‍💻", "다른 원문"})

    def test_invalid_backup_and_capacity_preserve_existing_bytes(self):
        self.store.merge([item()])
        original = self.path.read_bytes()
        for data in (b"{bad", b'{"version":2,"items":[]}', b'{"version":1,"items":[{}]}'):
            with self.assertRaises(ValueError):
                incoming = parse_backup(data)
                self.store.merge(incoming)
            self.assertEqual(self.path.read_bytes(), original)
        full = [item(id=str(i)) for i in range(500)]
        with self.assertRaises(ValueError):
            merge_libraries(full, [item(id="extra")])
        self.assertEqual(self.path.read_bytes(), original)

    def test_corrupt_existing_file_and_write_failure_never_overwrite(self):
        self.path.write_bytes(b"{not-json")
        with self.assertRaises(ValueError):
            self.store.merge([item()])
        self.assertEqual(self.path.read_bytes(), b"{not-json")
        self.path.unlink()
        self.store.merge([item()])
        original = self.path.read_bytes()
        with patch("optimizer_library.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                self.store.merge([item(id="two")])
        self.assertEqual(self.path.read_bytes(), original)

    def test_dialog_saves_and_confirms_restore(self):
        from optimizer_library import LibraryDialog
        app = QApplication.instance() or QApplication([])
        with patch("optimizer_library.LibraryStore", return_value=self.store):
            dialog = LibraryDialog(None, "원문", "편집한 결과", "general", "balanced", revisions=["추가 조건"])
        self.addCleanup(dialog.close)
        captured = []
        dialog.restored.connect(captured.append)
        dialog._save()
        self.assertEqual(self.store.load()[0]["result"], "편집한 결과")
        self.assertEqual(self.store.load()[0]["syeonRevisions"], ["추가 조건"])
        dialog._dialogs[-1].done(QMessageBox.StandardButton.Ok)
        dialog.entries.setCurrentRow(0)
        dialog._restore()
        dialog._dialogs[-1].done(QMessageBox.StandardButton.No)
        self.assertEqual(captured, [])
        dialog._restore()
        dialog._dialogs[-1].done(QMessageBox.StandardButton.Yes)
        self.assertEqual(captured[0]["prompt"], "원문")
        self.assertEqual(captured[0]["syeonRevisions"], ["추가 조건"])
        app.processEvents()

    def test_emoji_title_uses_utf16_limit(self):
        from optimizer_library import LibraryDialog
        app = QApplication.instance() or QApplication([])
        with patch("optimizer_library.LibraryStore", return_value=self.store):
            dialog = LibraryDialog(None, "😀" * 80, "결과")
        self.addCleanup(dialog.close)
        dialog._save()
        self.assertEqual(self.store.load()[0]["title"], "😀" * 60)
        self.assertEqual(self.store.load()[0]["prompt"], "😀" * 80)
        dialog._dialogs[-1].done(QMessageBox.StandardButton.Ok)
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
