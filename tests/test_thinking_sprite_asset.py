"""Validate the separately generated thinking atlas without touching user data."""
import hashlib
import json
from pathlib import Path
import unittest

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage


class ThinkingSpriteAssetTests(unittest.TestCase):
    def test_atlas_frames_are_distinct_and_transparent(self):
        root = Path(__file__).resolve().parents[1] / "assets" / "thinking"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        image = QImage(str(root / manifest["sprite_sheet_alpha"]))
        self.assertFalse(image.isNull())
        self.assertEqual((image.width(), image.height()), (1536, 256))
        rows = manifest["frame_layout"]["rows"]["thinking"]
        animation = manifest["animation"]["rows"]["thinking"]
        self.assertEqual(len(rows), 6)
        self.assertEqual(animation["durations_ms"], [250] * 6)
        self.assertTrue(animation["loop"])
        hashes = set()
        for rect in rows:
            self.assertEqual((rect["w"], rect["h"]), (256, 256))
            frame = image.copy(QRect(rect["x"], rect["y"], rect["w"], rect["h"]))
            self.assertEqual(frame.pixelColor(0, 0).alpha(), 0)
            self.assertGreater(frame.pixelColor(128, 128).alpha(), 200)
            hashes.add(hashlib.sha256(frame.constBits().tobytes()).hexdigest())
        self.assertGreaterEqual(len(hashes), 4)


if __name__ == "__main__":
    unittest.main()
