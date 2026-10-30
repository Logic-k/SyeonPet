"""Test reversible accessories and pack switching using isolated settings."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pet import ASSETS, PetWidget, SpriteBank
from wardrobe import WardrobeWindow, decorate_frame
from wardrobe_presets import PRESETS
from wardrobe_packs import import_sprite_pack


def pixels(pixmap):
    image = pixmap.toImage()
    return bytes(image.bits())


class WardrobeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.bank = SpriteBank(ASSETS)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / "wardrobe.ini"), QSettings.Format.IniFormat)
        self.pet = PetWidget(self.bank, wardrobe_settings=self.settings)
        self.pet.tick.stop()

    def tearDown(self):
        self.pet.close()
        self.app.processEvents()
        self.temp.cleanup()

    def test_every_animation_gets_a_reversible_accessory(self):
        for state, meta in self.bank.states.items():
            with self.subTest(state=state):
                original = meta["frames"][0]
                before = pixels(original)
                for choice in ("bow", "star", "cap"):
                    self.assertNotEqual(pixels(decorate_frame(original, choice)), before)
                self.assertEqual(pixels(original), before)
                self.assertEqual(pixels(decorate_frame(original, "original")), before)

    def test_chooser_persists_and_restores_without_mutating_source(self):
        window = WardrobeWindow(self.pet)
        original = pixels(self.bank.states["idle"]["frames"][0])
        window.buttons["bow"].click()
        self.assertEqual(self.pet.wardrobe_choice, "bow")
        self.assertEqual(self.settings.value("accessory"), "bow")
        self.assertNotEqual(pixels(self.pet._frame_pm(0)), original)
        self.pet.facing_left = True
        self.assertFalse(self.pet.mask().isEmpty())
        window.buttons["original"].click()
        self.pet.facing_left = False
        self.assertEqual(pixels(self.pet._frame_pm(0)), original)
        window.close()

    def test_saved_choice_and_malformed_settings(self):
        self.settings.setValue("accessory", "star")
        another = PetWidget(self.bank, wardrobe_settings=self.settings)
        another.tick.stop()
        self.assertEqual(another.wardrobe_choice, "star")
        another.close()
        self.settings.setValue("accessory", "invalid")
        another = PetWidget(self.bank, wardrobe_settings=self.settings)
        another.tick.stop()
        self.assertEqual(another.wardrobe_choice, "original")
        another.close()

    def test_premade_outfits_apply_and_survive_restart(self):
        window = WardrobeWindow(self.pet)
        self.assertGreaterEqual(len(PRESETS), 6)
        original = pixels(self.bank.states["idle"]["frames"][0])
        for choice in PRESETS:
            with self.subTest(choice=choice):
                window.buttons[choice].click()
                self.assertEqual(self.pet.wardrobe_choice, choice)
                self.assertEqual(self.settings.value("accessory"), choice)
                self.assertNotEqual(pixels(self.pet._frame_pm(0)), original)
        selected = self.pet.wardrobe_choice
        another = PetWidget(self.bank, wardrobe_settings=self.settings)
        another.tick.stop()
        self.assertEqual(another.wardrobe_choice, selected)
        another.close()
        window.buttons["original"].click()
        self.assertEqual(pixels(self.pet._frame_pm(0)), original)
        window.close()

    def test_imported_pack_restores_and_invalid_pack_does_not_replace_current(self):
        root = Path(self.temp.name) / "packs"
        with patch("pet.pack_root", return_value=root):
            imported = import_sprite_pack(ASSETS, root)
            self.pet.set_wardrobe_pack(imported.name)
            self.assertEqual(self.pet.wardrobe_pack, imported.name)
            with self.assertRaises(ValueError):
                self.pet.set_wardrobe_pack("../../bad")
            self.assertEqual(self.pet.wardrobe_pack, imported.name)
            another = PetWidget(self.bank, wardrobe_settings=self.settings)
            another.tick.stop()
            self.assertEqual(another.wardrobe_pack, imported.name)
            another.set_wardrobe_pack("original")
            self.assertIs(another.sprites, self.bank)
            self.assertTrue(imported.exists())
            another.close()


if __name__ == "__main__":
    unittest.main()
