"""New motion assets must remain usable in the portable app, not just the source tree."""
import hashlib
import json
from pathlib import Path
import unittest

from PySide6.QtGui import QImage

from tools.build_release import ASSETS


class ExtraMotionAssetTests(unittest.TestCase):
    def test_each_motion_is_packaged_nonempty_transparent_and_played_once(self):
        root = Path(__file__).resolve().parents[1]
        for state in ("stretch", "bow"):
            with self.subTest(state=state):
                relative = f"assets/extra_motions/{state}"
                for name in ("manifest.json", "sprite-sheet-alpha.png"):
                    self.assertIn(f"{relative}/{name}", ASSETS)
                manifest = json.loads((root / relative / "manifest.json").read_text(encoding="utf-8"))
                image = QImage(str(root / relative / "sprite-sheet-alpha.png"))
                self.assertFalse(image.isNull())
                animation = manifest["animation"]["rows"][state]
                rects = manifest["frame_layout"]["rows"][state]
                self.assertEqual(len(rects), 6)
                self.assertFalse(animation["loop"])
                self.assertEqual(len(animation["durations_ms"]), len(rects))
                self.assertTrue(all(100 <= ms <= 500 for ms in animation["durations_ms"]))
                poses = set()
                for rect in rects:
                    self.assertEqual((rect["w"], rect["h"]), (256, 256))
                    self.assertGreaterEqual(rect["x"], 0)
                    self.assertGreaterEqual(rect["y"], 0)
                    self.assertLessEqual(rect["x"] + rect["w"], image.width())
                    self.assertLessEqual(rect["y"] + rect["h"], image.height())
                    frame = image.copy(rect["x"], rect["y"], rect["w"], rect["h"])
                    self.assertGreater(frame.pixelColor(128, 128).alpha(), 200)
                    # A clear perimeter catches truncated leaves, hands and feet.
                    for pos in range(256):
                        for x, y in ((pos, 0), (pos, 255), (0, pos), (255, pos)):
                            self.assertEqual(frame.pixelColor(x, y).alpha(), 0)
                    poses.add(hashlib.sha256(frame.constBits().tobytes()).hexdigest())
                self.assertGreaterEqual(len(poses), 5)
