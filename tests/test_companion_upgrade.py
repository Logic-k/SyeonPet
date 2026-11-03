"""Unit and integration tests for centralized paths, companion care, quick bar, and shortcut helper."""
import datetime
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

import syeonpet.paths as paths
from syeonpet.pet.companion_care import (
    DEFAULT_CARE_LINES,
    HeartParticle,
    OfficeCareScheduler,
    PettingTracker,
    paint_hearts,
)
from syeonpet.pet.quick_bar import (
    QuickActionBar,
    TimerBadge,
    format_badge_text,
    paint_timer_badge,
)
import syeonpet.ui.shortcut_helper as shortcut_helper
import tools.shortcut_helper as tools_shortcut


class PathsTests(unittest.TestCase):
    """Verify centralized path resolutions and boundaries."""

    def test_app_root_and_assets(self):
        self.assertTrue(paths.APP_ROOT.is_dir())
        self.assertTrue((paths.APP_ROOT / "main.py").is_file())
        self.assertEqual(paths.ASSETS_DIR, paths.APP_ROOT / "assets")
        self.assertTrue(paths.ICON_PATH.is_file())

    def test_get_install_root(self):
        default_root = paths.get_install_root()
        self.assertEqual(default_root, paths.APP_ROOT)

        custom = paths.APP_ROOT / "custom_root"
        with patch.dict(os.environ, {"SYEONPET_INSTALL_ROOT": str(custom)}):
            self.assertEqual(paths.get_install_root(), custom.resolve())

    def test_find_libcrypto(self):
        # find_libcrypto returns either the direct or fallback libcrypto path
        resolved = paths.find_libcrypto()
        self.assertTrue(resolved.name == "libcrypto-3.dll")

        with tempfile.TemporaryDirectory() as td:
            dummy_root = Path(td)
            sp = dummy_root / "Lib" / "site-packages"
            sp.mkdir(parents=True)
            fake_dll = sp / "libcrypto-3.dll"
            fake_dll.write_bytes(b"DLL")
            self.assertEqual(paths.find_libcrypto(dummy_root), fake_dll)

    def test_user_data_dir_never_touches_app_root(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"LOCALAPPDATA": td}):
                data_dir = paths.user_data_dir()
                self.assertTrue(data_dir.is_dir())
                self.assertEqual(data_dir, (Path(td) / "SyeonPet").resolve())
                self.assertNotEqual(data_dir, paths.APP_ROOT)


class CompanionCareTests(unittest.TestCase):
    """Verify petting detection, heart particle physics, and office rhythm scheduling."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_petting_tracker_zone_filtering(self):
        tracker = PettingTracker(cooldown=2.0)
        sprite_h = 168.0

        # Strokes below head zone (y >= sprite_h * 0.55) must NOT trigger petting
        for x in [50, 70, 90, 110, 130]:
            triggered, msg = tracker.feed_point(x, sprite_h * 0.70, sprite_h, now=10.0)
            self.assertFalse(triggered)
            self.assertIsNone(msg)
        self.assertEqual(tracker.affection_points, 0)

    def test_petting_tracker_gesture_detection_and_cooldown(self):
        tracker = PettingTracker(cooldown=2.0)
        sprite_h = 168.0

        # Simulate horizontal gentle rubs in upper head zone (y = 40)
        now = 100.0
        moves = [40.0, 65.0, 95.0, 65.0, 35.0, 70.0, 100.0]
        triggered = False
        message = None
        for i, x in enumerate(moves):
            t, m = tracker.feed_point(x, 40.0, sprite_h, now=now + (i * 0.1))
            if t:
                triggered = True
                message = m
                break

        self.assertTrue(triggered)
        self.assertIsNotNone(message)
        self.assertEqual(tracker.affection_points, 1)
        self.assertGreater(len(tracker.particles), 0)

        # Immediate subsequent stroke within cooldown must NOT trigger
        t2, m2 = tracker.feed_point(50.0, 40.0, sprite_h, now=now + 0.8)
        self.assertFalse(t2)
        self.assertIsNone(m2)
        self.assertEqual(tracker.affection_points, 1)

    def test_affection_mood_labels(self):
        tracker = PettingTracker()
        tracker.affection_points = 0
        self.assertEqual(tracker.mood_label, "기분 좋음 🌱")
        tracker.affection_points = 4
        self.assertEqual(tracker.mood_label, "신남 💛")
        tracker.affection_points = 8
        self.assertEqual(tracker.mood_label, "사랑받는 중 💕")
        tracker.affection_points = 12
        self.assertEqual(tracker.mood_label, "집중 응원 중 🔥")

    def test_heart_particles_physics_and_painting(self):
        particle = HeartParticle(50.0, 50.0, vx=10.0, vy=-50.0, color=QColor(255, 120, 150), max_age=0.5)
        self.assertAlmostEqual(particle.alpha, 1.0, places=2)

        alive = particle.update(0.2)
        self.assertTrue(alive)
        self.assertLess(particle.y, 50.0)  # Floated upward
        self.assertLess(particle.alpha, 1.0)  # Faded

        alive = particle.update(0.4)
        self.assertFalse(alive)
        self.assertEqual(particle.alpha, 0.0)

        # Test paint_hearts on painter
        img = QImage(120, 120, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        painter = QPainter(img)
        particles = [
            HeartParticle(40.0, 40.0, 0, -20, QColor(255, 100, 100)),
            HeartParticle(60.0, 60.0, 0, -20, QColor(180, 140, 240)),
        ]
        paint_hearts(painter, particles)
        painter.end()

    def test_office_care_scheduler_periods(self):
        sched = OfficeCareScheduler()
        # Morning: 09:00
        dt_morn = datetime.datetime(2026, 11, 2, 9, 0)
        self.assertEqual(sched.get_period(dt_morn), "morning")

        # Lunch: 12:30
        dt_lunch = datetime.datetime(2026, 11, 2, 12, 30)
        self.assertEqual(sched.get_period(dt_lunch), "lunch")

        # Afternoon: 15:15
        dt_after = datetime.datetime(2026, 11, 2, 15, 15)
        self.assertEqual(sched.get_period(dt_after), "afternoon")

        # Overtime: 19:00
        dt_ot = datetime.datetime(2026, 11, 2, 19, 0)
        self.assertEqual(sched.get_period(dt_ot), "overtime")

        # General: 10:30
        dt_gen = datetime.datetime(2026, 11, 2, 10, 30)
        self.assertEqual(sched.get_period(dt_gen), "general")

    def test_office_care_scheduler_non_repeating(self):
        sched = OfficeCareScheduler(history_limit=4)
        dt = datetime.datetime(2026, 11, 2, 15, 0)
        seen = []
        for _ in range(5):
            msg, motion = sched.pick_care_message(dt)
            self.assertTrue(len(msg) > 0)
            seen.append(msg)
        # Successive messages must not be identical
        for i in range(1, len(seen)):
            self.assertNotEqual(seen[i], seen[i - 1])


class QuickActionBarTests(unittest.TestCase):
    """Verify quick action pill bar, signal emissions, edge clamping, and timer badge."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_buttons_and_signals(self):
        bar = QuickActionBar()
        signals_received = []

        bar.open_work.connect(lambda: signals_received.append("work"))
        bar.open_finder.connect(lambda: signals_received.append("finder"))
        bar.open_bag.connect(lambda: signals_received.append("bag"))
        bar.toggle_timer.connect(lambda: signals_received.append("timer"))
        bar.open_wardrobe.connect(lambda: signals_received.append("wardrobe"))

        bar.btn_work.click()
        bar.btn_finder.click()
        bar.btn_bag.click()
        bar.btn_timer.click()
        bar.btn_wardrobe.click()

        self.assertEqual(signals_received, ["work", "finder", "bag", "timer", "wardrobe"])
        bar.close()

    def test_set_timer_active_toggles_label(self):
        bar = QuickActionBar()
        self.assertEqual(bar.btn_timer.text(), "⏱️ 25분 집중")

        bar.set_timer_active(True)
        self.assertEqual(bar.btn_timer.text(), "⏱️ 타이머 중지")

        bar.set_timer_active(False)
        self.assertEqual(bar.btn_timer.text(), "⏱️ 25분 집중")
        bar.close()

    def test_fit_near_pet_positioning_and_clamping(self):
        bar = QuickActionBar()
        screen = QRect(0, 0, 1920, 1080)

        # Standard pet near bottom center: should sit directly above pet
        pet_geo = QRect(800, 800, 168, 168)
        bar.fit_near_pet(pet_geo, screen)
        self.assertLess(bar.y(), pet_geo.top())
        self.assertTrue(screen.contains(bar.geometry()))

        # Pet near top of screen: bar flips below pet
        pet_near_top = QRect(800, 10, 168, 168)
        bar.fit_near_pet(pet_near_top, screen)
        self.assertGreaterEqual(bar.y(), pet_near_top.bottom())
        self.assertTrue(screen.contains(bar.geometry()))

        # Pet near right edge: bar clamped within screen right
        pet_right = QRect(1850, 500, 168, 168)
        bar.fit_near_pet(pet_right, screen)
        self.assertLessEqual(bar.geometry().right(), screen.right())
        bar.close()

    def test_timer_badge_formatting_and_painting(self):
        self.assertEqual(format_badge_text("집중", 1499), "🍅 24:59")
        self.assertEqual(format_badge_text("focus", 60), "🍅 01:00")
        self.assertEqual(format_badge_text("휴식", 299), "☕ 04:59")
        self.assertEqual(format_badge_text("기타", 120), "⏱️ 02:00")

        # Paint badge on QPainter
        img = QImage(160, 60, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        painter = QPainter(img)
        paint_timer_badge(painter, QRect(10, 10, 100, 30), "🍅 24:59")
        painter.end()


class ShortcutHelperTests(unittest.TestCase):
    """Verify launcher resolution, PowerShell COM creation with Korean paths, and CLI."""

    def test_resolve_launcher_target(self):
        target = shortcut_helper.resolve_launcher_target()
        self.assertTrue(len(target["target_path"]) > 0)
        self.assertTrue(len(target["workdir"]) > 0)
        self.assertEqual(target["description"], shortcut_helper.SHORTCUT_DESC)

    def test_dry_run_creation(self):
        with tempfile.TemporaryDirectory() as td:
            p = shortcut_helper.create_desktop_shortcut(target_dir=Path(td), dry_run=True)
            self.assertEqual(p.name, shortcut_helper.SHORTCUT_NAME)
            self.assertFalse(p.is_file())

    def test_real_shortcut_creation_in_korean_temp_path(self):
        with tempfile.TemporaryDirectory() as td:
            korean_folder = Path(td) / "테스트_폴더_김민오"
            korean_folder.mkdir()
            res = shortcut_helper.create_desktop_shortcut(target_dir=korean_folder)
            self.assertTrue(res.is_file())
            self.assertGreater(res.stat().st_size, 0)
            self.assertEqual(res.name, "션 데스크탑 펫.lnk")

    def test_tools_reexport_and_cli_dry_run(self):
        self.assertTrue(hasattr(tools_shortcut, "create_desktop_shortcut"))
        with patch.object(sys, "argv", ["shortcut_helper.py", "--dry-run"]):
            rc = tools_shortcut.main()
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
