"""Internal transitions retain pose-fitted outfits and existing endpoints."""
import json
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from pet import ASSETS
from wardrobe_presets import PRESETS, _HEAD, _curated_frame, decorate_preset
import wardrobe_presets


def rgba(frame):
    image=frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return bytes(image.constBits())


def frames(folder, state):
    manifest=json.loads((folder/"manifest.json").read_text(encoding="utf8"))
    sheet=QPixmap(str(folder/"sprite-sheet-alpha.png"))
    return [sheet.copy(*(rect[k] for k in ("x","y","w","h")))
            for rect in manifest["frame_layout"]["rows"][state]]


class BridgeWardrobeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.rows={name:frames(ASSETS/"motion_bridges"/name,name)
                  for name in ("sleep_bridge","turn_bridge")}

    def test_all_transition_frames_use_curated_clothes_without_mutating_source(self):
        for choice in PRESETS:
            for name,row in self.rows.items():
                self.assertEqual(len(row),6)
                for i,frame in enumerate(row):
                    with self.subTest(choice=choice,state=name,index=i):
                        before=rgba(frame)
                        curated=_curated_frame(frame,choice)
                        self.assertIsNotNone(curated)
                        dressed=rgba(decorate_preset(frame,choice))
                        self.assertEqual(dressed,rgba(curated))
                        self.assertEqual(rgba(frame),before)
                        if choice not in _HEAD:
                            self.assertEqual(dressed[3::4],before[3::4])
                        self.assertLessEqual(len(wardrobe_presets._CURATED_SHEETS),2)

    def test_shared_idle_and_walk_endpoints_keep_original_outfits(self):
        idle=frames(ASSETS,"idle")[0]
        walk=frames(ASSETS,"walk")[0]
        for choice in PRESETS:
            for name in self.rows:
                self.assertEqual(rgba(self.rows[name][0]),rgba(idle))
                self.assertEqual(rgba(decorate_preset(self.rows[name][0],choice)),
                                 rgba(decorate_preset(idle,choice)))
            self.assertEqual(rgba(self.rows["turn_bridge"][-1]),rgba(walk))
            self.assertEqual(rgba(decorate_preset(self.rows["turn_bridge"][-1],choice)),
                             rgba(decorate_preset(walk,choice)))


if __name__=="__main__":
    unittest.main()
