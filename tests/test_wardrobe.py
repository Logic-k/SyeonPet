"""Test reversible accessories and pack switching using isolated settings."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QRect, QSettings, Qt
from PySide6.QtGui import QKeyEvent, QPainter, QPixmap
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

    def test_motion_preview_uses_real_frames_without_changing_pet_motion(self):
        window = WardrobeWindow(self.pet)
        try:
            self.pet.set_wardrobe_choice("bow")
            old_state, old_frame = self.pet.state, self.pet.frame_idx
            self.assertEqual(window.motion_combo.count(), len(self.bank.states))
            for state, meta in self.bank.states.items():
                with self.subTest(state=state):
                    meta = meta.get("playback", meta)
                    index = window.motion_combo.findData(state)
                    self.assertGreaterEqual(index, 0)
                    window.motion_combo.setCurrentIndex(index)
                    self.assertEqual(window.preview_slider.maximum(), len(meta["frames"]) - 1)
                    window.preview_slider.setValue(len(meta["frames"]) - 1)
                    self.assertEqual(window.preview_count.text(), f'{len(meta["frames"])} / {len(meta["frames"])}')
                    self.assertIn(window.preview_count.text(), window.preview_count.accessibleName())
                    # Preview must use the same costume/pose geometry as the pet.
                    expected = self.pet._frame_pm(len(meta["frames"]) - 1, state, False)
                    if offset := meta.get("render_offset_y", 0):
                        canvas = QPixmap(expected.size())
                        canvas.fill(Qt.GlobalColor.transparent)
                        painter = QPainter(canvas)
                        painter.drawPixmap(0, offset, expected)
                        painter.end()
                        expected = canvas
                    expected = expected.scaled(
                        150, 150, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
                    self.assertEqual(pixels(window.preview.pixmap()), pixels(expected))
            self.assertEqual((self.pet.state, self.pet.frame_idx), (old_state, old_frame))
        finally:
            window.close()

    def test_motion_preview_includes_recovery_pose_and_its_duration(self):
        window = WardrobeWindow(self.pet)
        try:
            for state in ("stretch", "bow"):
                with self.subTest(state=state):
                    window.motion_combo.setCurrentIndex(window.motion_combo.findData(state))
                    window.preview_slider.setValue(3)
                    window._preview_playing = True
                    window._advance_preview()
                    self.assertEqual(window.preview_slider.value(), 4)
                    self.assertEqual(window._preview_timer.interval(), 140)
                    self.assertEqual(window.preview_slider.maximum(), 6)
                    expected = decorate_frame(self.bank.states[state]["frames"][2],
                                              self.pet.wardrobe_choice).scaled(
                        150, 150, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
                    self.assertEqual(pixels(window.preview.pixmap()), pixels(expected))
        finally:
            window.close()

    def test_motion_preview_play_pause_flip_and_close(self):
        window = WardrobeWindow(self.pet)
        try:
            self.assertIn("반복", window.preview_play.accessibleDescription())
            window.motion_combo.setCurrentIndex(window.motion_combo.findData("walk"))
            unflipped = pixels(window.preview.pixmap())
            window.preview_flip.setChecked(True)
            self.assertNotEqual(pixels(window.preview.pixmap()), unflipped)
            window.preview_play.click()
            self.assertTrue(window._preview_playing)
            self.assertTrue(window._preview_timer.isActive())
            window.motion_combo.setCurrentIndex(window.motion_combo.findData("sleep"))
            self.assertTrue(window._preview_playing)
            self.assertTrue(window._preview_timer.isActive())
            self.assertEqual(window.preview_slider.value(), 0)
            before = window.preview_slider.value()
            window._advance_preview()
            self.assertEqual(window.preview_slider.value(), before + 1)
            window.preview_play.click()
            self.assertFalse(window._preview_playing)
            self.assertFalse(window._preview_timer.isActive())
            window.preview_play.click()
            window.close()
            self.assertFalse(window._preview_timer.isActive())
            self.assertEqual(window.preview_play.text(), "재생")
        finally:
            window.close()

    def test_hiding_window_stops_preview_animation(self):
        window = WardrobeWindow(self.pet)
        try:
            window.show()
            self.app.processEvents()
            window.preview_play.click()
            self.assertTrue(window._preview_timer.isActive())
            window.hide()
            self.app.processEvents()
            self.assertFalse(window._preview_playing)
            self.assertFalse(window._preview_timer.isActive())
            self.assertEqual(window.preview_play.text(), "재생")
        finally:
            window.close()

    def test_compact_work_area_and_long_choices_do_not_add_horizontal_scroll(self):
        window = WardrobeWindow(self.pet)
        try:
            with patch.object(window, "_fit_to_work_area"):
                window.show()
                self.app.processEvents()
                window.pack_combo.addItem("가져온 외형 " * 20, "long-display-only")
                window.pack_combo.setCurrentIndex(window.pack_combo.count() - 1)
                for bounds in (QRect(-700, 20, 360, 360), QRect(-700, 20, 300, 300)):
                    with self.subTest(bounds=bounds):
                        window._fit_to_bounds(bounds)
                        self.app.processEvents()
                        self.assertTrue(bounds.contains(window.frameGeometry()),
                                        f"{window.frameGeometry()} outside {bounds}")
                        self.assertEqual(window.scroll.horizontalScrollBar().maximum(), 0)
                        self.assertGreater(window.scroll.verticalScrollBar().maximum(), 0)
                window._fit_to_bounds(QRect(0, 0, 1400, 900))
                self.assertEqual(window.minimumWidth(), 350)
                self.assertEqual(window.minimumHeight(), 300)
        finally:
            window.close()

    def test_keyboard_order_scrolls_controls_into_view_in_both_directions(self):
        window = WardrobeWindow(self.pet)
        try:
            window.resize(350, 300)
            window.show()
            self.app.processEvents()
            self.assertFalse(window._preview_playing)
            self.assertFalse(window._preview_timer.isActive())
            order = (window.pack_combo, window.add_pack, window.choice_combo,
                     window.motion_combo, window.preview_play, window.preview_flip,
                     window.preview_slider, window.buttons[self.pet.wardrobe_choice],
                     window.close_button)
            window.pack_combo.setFocus()
            for previous, following in zip(order, order[1:]):
                QApplication.sendEvent(previous, QKeyEvent(
                    QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier))
                self.app.processEvents()
                self.assertTrue(following.hasFocus(), following.accessibleName())
                self.assertTrue(following.accessibleName())
                top = following.mapTo(window.scroll.viewport(), following.rect().topLeft()).y()
                self.assertGreaterEqual(top, 0)
                self.assertLess(top + following.height(), window.scroll.viewport().height() + 1)
            for following, previous in zip(reversed(order), list(reversed(order))[1:]):
                QApplication.sendEvent(following, QKeyEvent(
                    QEvent.Type.KeyPress, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier))
                self.app.processEvents()
                self.assertTrue(previous.hasFocus(), previous.accessibleName())
            self.assertEqual(window.scroll.focusPolicy(), Qt.FocusPolicy.NoFocus)
        finally:
            window.close()

    def test_control_boxes_stay_stable_across_focus_hover_disabled_and_play(self):
        window = WardrobeWindow(self.pet)
        try:
            window.show()
            self.app.processEvents()
            controls = (window.pack_combo, window.add_pack, window.preview_play, window.close_button)
            sizes = [control.size() for control in controls]
            for control in controls:
                control.setFocus()
                QApplication.sendEvent(control, QEvent(QEvent.Type.Enter))
                self.app.processEvents()
                self.assertEqual([item.size() for item in controls], sizes)
                QApplication.sendEvent(control, QEvent(QEvent.Type.Leave))
                control.setEnabled(False)
                self.app.processEvents()
                self.assertEqual([item.size() for item in controls], sizes)
                control.setEnabled(True)
            window.preview_play.click()
            self.app.processEvents()
            self.assertTrue(window._preview_playing)
            self.assertEqual([item.size() for item in controls], sizes)
            window.preview_play.click()
            self.app.processEvents()
            self.assertEqual([item.size() for item in controls], sizes)
        finally:
            window.close()

    def test_quick_choice_and_existing_list_remain_in_sync(self):
        window = WardrobeWindow(self.pet)
        try:
            window.choice_combo.setCurrentIndex(window.choice_combo.findData("bow"))
            self.assertTrue(window.buttons["bow"].isChecked())
            self.assertEqual(self.pet.wardrobe_choice, "bow")
            window.buttons["star"].click()
            self.assertEqual(window.choice_combo.currentData(), "star")
            self.assertEqual(self.pet.wardrobe_choice, "star")
            window.choice_combo.setCurrentIndex(window.choice_combo.findData("original"))
            self.assertTrue(window.buttons["original"].isChecked())
            self.assertEqual(self.pet.wardrobe_choice, "original")
        finally:
            window.close()

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
        with patch("pet.pack_root", return_value=root), patch("wardrobe.pack_root", return_value=root):
            imported = import_sprite_pack(ASSETS, root)
            self.pet.set_wardrobe_pack(imported.name)
            self.assertEqual(self.pet.wardrobe_pack, imported.name)
            with self.assertRaises(ValueError):
                self.pet.set_wardrobe_pack("../../bad")
            self.assertEqual(self.pet.wardrobe_pack, imported.name)
            another = PetWidget(self.bank, wardrobe_settings=self.settings)
            another.tick.stop()
            self.assertEqual(another.wardrobe_pack, imported.name)
            window = WardrobeWindow(another)
            window.motion_combo.setCurrentIndex(window.motion_combo.findData("walk"))
            window.pack_combo.setCurrentIndex(window.pack_combo.findData("original"))
            self.assertEqual(window.motion_combo.currentData(), "walk")
            self.assertEqual(window.preview_slider.maximum(), len(self.bank.states["walk"]["frames"]) - 1)
            self.assertEqual(another.wardrobe_pack, "original")
            window.close()
            another.set_wardrobe_pack("original")
            self.assertIs(another.sprites, self.bank)
            self.assertTrue(imported.exists())
            another.close()


if __name__ == "__main__":
    unittest.main()
