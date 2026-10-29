"""Optional Sprite-Gen thinking row loading and safe fallback."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pet import ASSETS, SpriteBank


class ThinkingAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.assets = Path(self.temp.name)
        for name in ("manifest.json", "sprite-sheet-alpha.png"):
            shutil.copy2(ASSETS / name, self.assets / name)
        self.folder = self.assets / "thinking"
        self.folder.mkdir()
        for name in ("manifest.json", "sprite-sheet-alpha.png"):
            shutil.copy2(ASSETS / "thinking" / name, self.folder / name)

    def change_manifest(self, mutate):
        path = self.folder / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_actual_generated_row_has_six_nonempty_frames(self):
        bank = SpriteBank(self.assets)
        self.assertTrue(bank.has("idle"))
        self.assertTrue(bank.has("thinking"))
        meta = bank.states["thinking"]
        self.assertEqual(len(meta["frames"]), 6)
        self.assertEqual(meta["durations"], [250] * 6)
        self.assertTrue(meta["loop"])
        self.assertTrue(all(not frame.isNull() for frame in meta["frames"]))

    def test_optional_missing_or_invalid_assets_leave_original_states_usable(self):
        for target in ("manifest.json", "sprite-sheet-alpha.png"):
            with self.subTest(missing=target):
                path = self.folder / target
                backup = path.read_bytes()
                path.unlink()
                bank = SpriteBank(self.assets)
                self.assertTrue(bank.has("idle"))
                self.assertFalse(bank.has("thinking"))
                path.write_bytes(backup)
        (self.folder / "manifest.json").write_text("not json", encoding="utf-8")
        self.assertFalse(SpriteBank(self.assets).has("thinking"))

    def test_invalid_rectangles_durations_or_loop_are_rejected(self):
        original = (self.folder / "manifest.json").read_text(encoding="utf-8")
        cases = (
            lambda m: m["frame_layout"]["rows"]["thinking"][0].update(x=-1),
            lambda m: m["frame_layout"]["rows"]["thinking"][0].update(x=99999),
            lambda m: m["animation"]["rows"]["thinking"]["durations_ms"].__setitem__(0, 0),
            lambda m: m["animation"]["rows"]["thinking"].update(loop=False),
            lambda m: m["frame_layout"]["rows"]["thinking"].pop(),
        )
        for mutate in cases:
            with self.subTest(mutate=mutate):
                (self.folder / "manifest.json").write_text(original, encoding="utf-8")
                self.change_manifest(mutate)
                bank = SpriteBank(self.assets)
                self.assertTrue(bank.has("idle"))
                self.assertFalse(bank.has("thinking"))

    def test_manifest_filename_is_not_followed(self):
        self.change_manifest(lambda m: m.update(sprite_sheet_alpha="../../untrusted.png"))
        self.assertTrue(SpriteBank(self.assets).has("thinking"))


if __name__ == "__main__":
    unittest.main()
