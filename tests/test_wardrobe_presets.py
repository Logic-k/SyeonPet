"""Preset assets preserve the existing sprites and important foreground."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from pet import ASSETS, PetWidget, SpriteBank
from wardrobe_presets import PRESETS, decorate_preset
import wardrobe_presets


def rgba(pixmap):
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return bytes(image.constBits())


class WardrobePresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.bank = SpriteBank(ASSETS)

    def test_head_presets_follow_all_existing_frames_without_mutation(self):
        states = self.bank.states
        self.assertEqual(sum(len(meta["frames"]) for meta in states.values()), 81)
        for name in ("beret", "sunhat", "ribbon_hat"):
            for state, meta in states.items():
                for frame in meta["frames"]:
                    original = rgba(frame)
                    with self.subTest(state=state, name=name):
                        self.assertNotEqual(rgba(decorate_preset(frame, name)), original)
                        self.assertEqual(rgba(frame), original)

    def test_body_presets_preserve_saturated_foreground_and_silhouette(self):
        for name in ("scarf_blue", "scarf_pink", "cardigan", "apron"):
            for state, meta in self.bank.states.items():
                for frame in meta["frames"]:
                    original = rgba(frame)
                    dressed = rgba(decorate_preset(frame, name))
                    self.assertEqual(len(dressed), len(original))
                    self.assertEqual(dressed[3::4], original[3::4],
                                     f"{name} changed alpha in {state}")
                    for i in range(0, len(original), 4):
                        r, g, b, a = original[i:i + 4]
                        # Neutral garment shadows can change to create a
                        # continuous cloth surface. Keep saturated foreground
                        # (bag, cheeks, leaf, beak/feet) and dark outlines.
                        if (a > 230 and max(r, g, b) - min(r, g, b) >= 50) or max(r, g, b) < 90:
                            if dressed[i:i + 4] != original[i:i + 4]:
                                self.fail(f"{name} changed foreground in {state}")

    def test_curated_atlases_cover_every_original_frame(self):
        for name in PRESETS:
            for state, meta in self.bank.states.items():
                for frame in meta["frames"]:
                    self.assertIsNotNone(wardrobe_presets._curated_frame(frame, name),
                                         f"{name}: {state} missing curated frame")
                    self.assertLessEqual(len(wardrobe_presets._CURATED_SHEETS), 2)

    def test_changed_user_frame_uses_existing_dynamic_fallback(self):
        original = self.bank.states["idle"]["frames"][0].copy()
        painter = QPainter(original)
        painter.setPen(QColor("#dd3412"))
        painter.drawPoint(1, 1)
        painter.end()
        before = rgba(original)
        self.assertIsNone(wardrobe_presets._curated_frame(original, "beret"))
        self.assertNotEqual(rgba(decorate_preset(original, "beret")), before)
        self.assertEqual(rgba(original), before)

    def test_preset_choice_is_persistent_and_original_is_reversible(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "wardrobe.ini"), QSettings.Format.IniFormat)
            settings.setFallbacksEnabled(False)
            pet = PetWidget(self.bank, wardrobe_settings=settings)
            pet.tick.stop()
            try:
                before = rgba(self.bank.states[pet.state]["frames"][0])
                pet.set_wardrobe_choice("beret")
                self.assertEqual(settings.value("accessory"), "beret")
                self.assertNotEqual(rgba(pet._frame_pm(0)), before)
                pet.set_wardrobe_choice("original")
                self.assertEqual(rgba(pet._frame_pm(0)), before)
                self.assertFalse(pet.mask().isEmpty())
            finally:
                pet.close()

    def test_unrecognized_choice_does_not_change_pixels(self):
        frame = self.bank.states["idle"]["frames"][0]
        self.assertEqual(rgba(decorate_preset(frame, "../../unknown")), rgba(frame))
        self.assertGreaterEqual(len(PRESETS), 6)

    def test_precomputed_anchors_match_dynamic_detector_for_every_frame(self):
        self.assertGreaterEqual(len(wardrobe_presets._known_anchors()), 70)
        for state, meta in self.bank.states.items():
            for frame in meta["frames"]:
                with self.subTest(state=state):
                    wardrobe_presets._POSITION_CACHE.clear()
                    known = wardrobe_presets._anchors(frame)
                    wardrobe_presets._POSITION_CACHE.clear()
                    with patch.object(wardrobe_presets, "_PRECOMPUTED", {}):
                        dynamic = wardrobe_presets._anchors(frame)
                    self.assertEqual(known, dynamic)
        wardrobe_presets._POSITION_CACHE.clear()

    def test_missing_anchor_file_uses_dynamic_detection(self):
        frame = self.bank.states["idle"]["frames"][0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for text in (None, '{"schema":1,"frames":{"bad":[]}}'):
                anchor_file = path / "anchors.json"
                if text is not None:
                    anchor_file.write_text(text, encoding="utf-8")
                wardrobe_presets._POSITION_CACHE.clear()
                with patch.object(wardrobe_presets, "_ASSETS", path), patch.object(
                        wardrobe_presets, "_PRECOMPUTED", None):
                    self.assertEqual(wardrobe_presets._known_anchors(), {})
                    self.assertIsNotNone(wardrobe_presets._anchors(frame))
        wardrobe_presets._POSITION_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
