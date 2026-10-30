"""Local-only Sprite-Gen atlas import checks using the project's own assets."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Lib" / "site-packages"))

from wardrobe_packs import WardrobePackError, import_sprite_pack, validate_sprite_pack


class WardrobePackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.source = self.base / "source"
        (self.source / "thinking").mkdir(parents=True)
        for name in ("manifest.json", "sprite-sheet-alpha.png",
                     "thinking/manifest.json", "thinking/sprite-sheet-alpha.png"):
            shutil.copyfile(ROOT / "assets" / name, self.source / name)

    def tearDown(self):
        self.tmp.cleanup()

    def edit_manifest(self, edit):
        path = self.source / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        edit(data)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_actual_atlases_are_accepted_and_independent_copies_are_created(self):
        validate_sprite_pack(self.source)
        original = (self.source / "sprite-sheet-alpha.png").read_bytes()
        store = self.base / "packs"
        first = import_sprite_pack(self.source, store)
        second = import_sprite_pack(self.source, store)
        self.assertRegex(first.name, r"^pack-[0-9a-f]{32}$")
        self.assertNotEqual(first, second)
        self.assertEqual((first / "sprite-sheet-alpha.png").read_bytes(), original)
        self.assertEqual((self.source / "sprite-sheet-alpha.png").read_bytes(), original)
        validate_sprite_pack(first)

    def test_missing_thinking_motion_is_rejected_without_partial_pack(self):
        (self.source / "thinking" / "manifest.json").unlink()
        store = self.base / "packs"
        with self.assertRaises(WardrobePackError):
            import_sprite_pack(self.source, store)
        self.assertFalse(store.exists())

    def test_missing_animation_or_bad_rectangle_is_rejected(self):
        self.edit_manifest(lambda x: x["frame_layout"]["rows"].pop("walk"))
        with self.assertRaises(WardrobePackError):
            validate_sprite_pack(self.source)
        shutil.copyfile(ROOT / "assets" / "manifest.json", self.source / "manifest.json")
        self.edit_manifest(lambda x: x["frame_layout"]["rows"]["idle"][0].update(x=-1))
        with self.assertRaises(WardrobePackError):
            validate_sprite_pack(self.source)

    def test_inconsistent_timing_and_unknown_state_are_rejected(self):
        self.edit_manifest(lambda x: x["animation"]["rows"]["idle"]["durations_ms"].clear())
        with self.assertRaises(WardrobePackError):
            validate_sprite_pack(self.source)
        shutil.copyfile(ROOT / "assets" / "manifest.json", self.source / "manifest.json")
        self.edit_manifest(lambda x: x["frame_layout"]["rows"].update(extra=[]))
        with self.assertRaises(WardrobePackError):
            validate_sprite_pack(self.source)

    def test_nonlooping_thinking_is_rejected_because_spritebank_cannot_load_it(self):
        path = self.source / "thinking" / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["animation"]["rows"]["thinking"]["loop"] = False
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(WardrobePackError):
            validate_sprite_pack(self.source)

    def test_corrupt_and_oversized_png_are_rejected(self):
        png = self.source / "sprite-sheet-alpha.png"
        png.write_bytes(b"not-a-png")
        with self.assertRaises(WardrobePackError):
            validate_sprite_pack(self.source)
        # Header says an unreasonably large image: rejection happens before decode.
        png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (999999).to_bytes(4, "big") * 2 + b"\x00" * 20)
        with self.assertRaises(WardrobePackError):
            validate_sprite_pack(self.source)

    def test_duplicate_manifest_key_and_symlink_are_rejected(self):
        (self.source / "manifest.json").write_text('{"frame_layout":{},"frame_layout":{}}', encoding="utf-8")
        with self.assertRaises(WardrobePackError):
            validate_sprite_pack(self.source)
        shutil.copyfile(ROOT / "assets" / "manifest.json", self.source / "manifest.json")
        target = self.source / "thinking" / "sprite-sheet-alpha.png"
        target.unlink()
        try:
            target.symlink_to(ROOT / "assets" / "thinking" / "sprite-sheet-alpha.png")
        except OSError:
            self.skipTest("현재 Windows 계정에서 심볼릭 링크를 만들 수 없습니다")
        with self.assertRaises(WardrobePackError):
            validate_sprite_pack(self.source)


if __name__ == "__main__":
    unittest.main()
