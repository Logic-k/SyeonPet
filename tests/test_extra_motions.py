"""Optional gesture rows and their ownership rules use isolated test state."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from behavior import BehaviorController
from pet import ASSETS, PetWidget, SpriteBank
from wardrobe_packs import import_sprite_pack
from test_pet_motion import FakeBank


def write_row(folder, state, *, loop=False):
    folder.mkdir(parents=True, exist_ok=True)
    sheet = QPixmap(96, 16)
    sheet.fill(Qt.GlobalColor.transparent)
    painter = QPainter(sheet)
    for i in range(6):
        painter.fillRect(i * 16 + 2, 3, 10, 10, QColor(30 + i * 30, 80, 120))
    painter.end()
    sheet.save(str(folder / "sprite-sheet-alpha.png"))
    manifest = {
        "animation": {"rows": {state: {
            "frames": 6, "loop": loop, "durations_ms": [100, 120, 140, 160, 180, 200],
        }}},
        "frame_layout": {"rows": {state: [
            {"x": i * 16, "y": 0, "w": 16, "h": 16} for i in range(6)
        ]}},
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class ExtraMotionAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.assets = Path(self.temporary.name)
        write_row(self.assets, "idle", loop=True)
        self.folder = self.assets / "extra_motions" / "stretch"
        self.manifest = write_row(self.folder, "stretch")
        write_row(self.assets / "extra_motions" / "bow", "bow")

    def tearDown(self):
        self.temporary.cleanup()

    def test_rows_keep_their_frame_timing_and_do_not_loop(self):
        bank = SpriteBank(self.assets)
        for name in ("stretch", "bow"):
            with self.subTest(name=name):
                self.assertEqual(len(bank.states[name]["frames"]), 6)
                self.assertEqual(bank.states[name]["durations"], [100, 120, 140, 160, 180, 200])
                self.assertFalse(bank.states[name]["loop"])
        self.assertTrue(bank.has("idle"))

    def test_missing_or_corrupt_row_leaves_other_gestures_available(self):
        path = self.folder / "manifest.json"
        for content in (None, "invalid JSON"):
            with self.subTest(content=content):
                if content is None:
                    path.unlink()
                else:
                    path.write_text(content, encoding="utf-8")
                bank = SpriteBank(self.assets)
                self.assertFalse(bank.has("stretch"))
                self.assertTrue(bank.has("bow"))
                self.assertTrue(bank.has("idle"))

    def test_invalid_duration_bounds_and_loop_are_rejected(self):
        cases = (
            lambda m: m["animation"]["rows"]["stretch"].update(loop=True),
            lambda m: m["animation"]["rows"]["stretch"]["durations_ms"].pop(),
            lambda m: m["animation"]["rows"]["stretch"]["durations_ms"].__setitem__(0, 0),
            lambda m: m["animation"]["rows"]["stretch"]["durations_ms"].__setitem__(0, True),
            lambda m: m["animation"]["rows"]["stretch"]["durations_ms"].__setitem__(0, 10001),
            lambda m: m["frame_layout"]["rows"]["stretch"][0].update(x=-1),
            lambda m: m["frame_layout"]["rows"]["stretch"][0].update(x=96),
            lambda m: m["frame_layout"]["rows"]["stretch"][0].update(w=15),
            lambda m: m["frame_layout"]["rows"]["stretch"].pop(),
        )
        for mutate in cases:
            value = json.loads(json.dumps(self.manifest))
            mutate(value)
            (self.folder / "manifest.json").write_text(json.dumps(value), encoding="utf-8")
            bank = SpriteBank(self.assets)
            self.assertFalse(bank.has("stretch"))
            self.assertTrue(bank.has("idle"))

    def test_sheet_name_from_manifest_is_not_followed(self):
        self.manifest["sprite_sheet_alpha"] = "../../untrusted.png"
        (self.folder / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        self.assertTrue(SpriteBank(self.assets).has("stretch"))


class ExtraMotionBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        settings = QSettings(str(Path(self.temporary.name) / "test.ini"), QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        bank = FakeBank()
        for name in ("sleep", "liesleep", "drag", "thinking"):
            bank.states[name] = bank.states["idle"].copy()
        for name in ("stretch", "bow"):
            bank.states[name] = {"frames": bank.states["idle"]["frames"] * 3,
                                 "durations": [100, 120, 140, 160, 180, 200], "loop": False}
        self.pet = PetWidget(bank, display_size=32, wardrobe_settings=settings)
        self.behavior = BehaviorController(self.pet)
        self.pet.tick.stop()
        self.behavior.timer.stop()

    def tearDown(self):
        self.pet.tick.stop()
        self.behavior.timer.stop()
        self.pet.close()
        self.app.processEvents()
        self.temporary.cleanup()

    def test_each_action_finishes_in_place_and_returns_to_idle(self):
        for name in ("stretch", "bow"):
            with self.subTest(name=name):
                start = self.pet.pos()
                self.behavior.run_action(name)
                self.assertEqual((self.behavior.mode, self.pet.state), ("oneshot:idle", name))
                self.pet._advance_frame(899)
                self.assertFalse(self.pet.one_shot_finished())
                self.pet._advance_frame(1)
                self.assertTrue(self.pet.one_shot_finished())
                self.behavior._tick()
                self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))
                self.assertEqual(self.pet.pos(), start)

    def test_repeat_selection_restarts_the_gesture(self):
        self.behavior.play_extra_motion("bow")
        self.pet._advance_frame(500)
        self.assertGreater(self.pet.frame_idx, 0)
        self.behavior.play_extra_motion("bow")
        self.assertEqual((self.pet.frame_idx, self.pet._frame_elapsed), (0, 0))

    def test_sleep_thinking_drag_tour_and_reduction_keep_their_state(self):
        cases = (
            self.behavior.sleep, self.behavior.deep_sleep, self.behavior.still_sleep,
            self.behavior.begin_thinking,
            lambda: setattr(self.behavior, "onboarding_active", True),
            lambda: setattr(self.pet, "_dragging", True),
            lambda: setattr(self.behavior, "_fell", True),
            lambda: self.pet.set_reduced_motion(True),
        )
        for setup in cases:
            with self.subTest(setup=setup):
                self.pet.set_reduced_motion(False)
                self.pet.tick.stop()
                self.pet._dragging = False
                self.behavior._fell = False
                self.behavior.onboarding_active = False
                self.behavior.idle()
                setup()
                before = (self.behavior.mode, self.pet.state, self.behavior._thinking_restore)
                for name in ("stretch", "bow"):
                    self.assertFalse(self.behavior.play_extra_motion(name))
                    self.assertEqual((self.behavior.mode, self.pet.state,
                                      self.behavior._thinking_restore), before)

    def test_missing_asset_does_not_leave_a_never_ending_one_shot(self):
        self.pet.sprites.states.pop("stretch")
        self.behavior.run_action("stretch")
        self.behavior.play_one_shot("stretch")
        self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))

    def test_imported_pack_without_extra_rows_finishes_an_interrupted_gesture(self):
        packs = Path(self.temporary.name) / "packs"
        imported = import_sprite_pack(ASSETS, packs)
        for name in ("stretch", "bow"):
            with self.subTest(name=name), patch("pet.pack_root", return_value=packs):
                self.pet.set_wardrobe_pack("original")
                self.behavior.play_extra_motion(name)
                self.pet._advance_frame(300)
                self.assertFalse(self.pet.one_shot_finished())
                self.pet.set_wardrobe_pack(imported.name)
                self.assertEqual(self.pet.state, "idle")
                self.assertEqual(self.pet.wardrobe_pack, imported.name)
                self.assertFalse(self.pet.sprites.has(name))
                self.pet._advance_frame(200)
                self.behavior._tick()
                self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))

    def test_reduced_motion_during_gesture_returns_to_idle_without_moving(self):
        self.behavior.play_extra_motion("stretch")
        start = self.pet.pos()
        self.pet.set_reduced_motion(True)
        self.behavior._tick()
        self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))
        self.assertEqual(self.pet.pos(), start)

    def test_pack_switch_while_thinking_does_not_restore_a_missing_gesture(self):
        packs = Path(self.temporary.name) / "packs"
        imported = import_sprite_pack(ASSETS, packs)
        for name in ("stretch", "bow"):
            with self.subTest(name=name), patch("pet.pack_root", return_value=packs):
                self.pet.set_wardrobe_pack("original")
                self.behavior.play_extra_motion(name)
                self.pet._advance_frame(850)
                self.behavior.begin_thinking()
                self.pet.set_wardrobe_pack(imported.name)
                self.behavior._thinking_started -= 1
                self.behavior.end_thinking()
                self.assertEqual((self.behavior.mode, self.pet.state), ("idle", "idle"))
                self.assertEqual((self.pet.frame_idx, self.pet._frame_elapsed), (0, 0))
                self.assertIsNone(self.behavior._thinking_restore)
                self.pet._advance_frame(1000)
                self.behavior._tick()
                self.assertEqual(self.behavior.mode, "idle")

    def test_thinking_restores_gesture_frame_and_then_finishes(self):
        self.behavior.play_extra_motion("bow")
        self.pet._advance_frame(240)
        saved = (self.pet.frame_idx, self.pet._frame_elapsed)
        self.behavior.begin_thinking()
        self.behavior._thinking_started -= 1
        self.behavior.end_thinking()
        self.assertEqual((self.behavior.mode, self.pet.state), ("oneshot:idle", "bow"))
        self.assertEqual((self.pet.frame_idx, self.pet._frame_elapsed), saved)
        self.pet._advance_frame(1000)
        self.behavior._tick()
        self.assertEqual(self.behavior.mode, "idle")

    def test_idle_variety_uses_only_an_available_gesture(self):
        self.pet.sprites.states.pop("bow")
        self.behavior.mode_until = 0
        with patch("behavior.random.random", side_effect=[0.9, 0.1]):
            self.behavior._tick()
        self.assertEqual(self.pet.state, "stretch")


if __name__ == "__main__":
    unittest.main()
