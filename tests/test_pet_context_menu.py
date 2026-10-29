"""Pet context menu: real Qt events, shared actions and screen bounds."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu

import main
from behavior import BehaviorController
from pet import PetWidget, SpriteBank


class PetContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.sprites = SpriteBank()

    def setUp(self):
        self.pet = PetWidget(self.sprites)
        self.behavior = BehaviorController(self.pet)
        self.pet.tick.stop()
        self.behavior.timer.stop()
        self.menu = QMenu()
        self.sleep_action = self.menu.addAction("완전히 가만히 재우기")
        self.sleep_action.triggered.connect(self.behavior.still_sleep)
        self.calm_action = self.menu.addAction("차분 모드")
        self.calm_action.setCheckable(True)
        main.bind_pet_menu(self.pet, self.menu)

    def tearDown(self):
        self.menu.close()
        self.pet.close()
        self.app.processEvents()

    def test_right_click_uses_shared_menu_and_does_not_start_drag(self):
        local = QPoint(150, 70)
        global_pos = self.pet.mapToGlobal(local)
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, local, global_pos)
        with patch.object(main, "popup_pet_menu") as popup:
            QApplication.sendEvent(self.pet, event)
        popup.assert_called_once_with(self.menu, self.pet, global_pos)
        self.assertFalse(self.pet._dragging)
        self.assertEqual(self.behavior.mode, "idle")
        # The same QAction is used regardless of whether it was opened from
        # the pet or the tray: checking/action state is not duplicated.
        self.calm_action.setChecked(True)
        self.assertTrue(self.menu.actions()[1].isChecked())
        self.sleep_action.trigger()
        self.assertTrue(self.behavior.fully_asleep)
        with patch.object(main, "popup_pet_menu") as popup:
            QApplication.sendEvent(self.pet, QContextMenuEvent(
                QContextMenuEvent.Reason.Mouse, local, global_pos))
        popup.assert_called_once()
        self.assertTrue(self.behavior.fully_asleep)

    def test_popup_position_is_clamped_to_monitor_work_area(self):
        area = QRect(1000, 600, 300, 200)
        screen = MagicMock()
        screen.availableGeometry.return_value = area
        gui = MagicMock()
        gui.screenAt.return_value = screen
        menu = MagicMock()
        menu.sizeHint.return_value = QSize(100, 80)
        with patch.object(main, "QGuiApplication", gui):
            main.popup_pet_menu(menu, self.pet, QPoint(1299, 799))
        menu.popup.assert_called_once_with(QPoint(1200, 720))

    def test_popup_falls_back_to_pet_screen_for_outside_position(self):
        area = QRect(0, 0, 100, 60)
        screen = MagicMock()
        screen.availableGeometry.return_value = area
        gui = MagicMock()
        gui.screenAt.return_value = None
        pet = MagicMock()
        pet.screen.return_value = screen
        menu = MagicMock()
        # When taller than the work area, Qt scrolls the menu; anchor at top.
        menu.sizeHint.return_value = QSize(40, 90)
        with patch.object(main, "QGuiApplication", gui):
            main.popup_pet_menu(menu, pet, QPoint(-10, -20))
        menu.popup.assert_called_once_with(QPoint(0, 0))

    def test_update_background_stays_quiet_except_new_release(self):
        tray = MagicMock()
        for status in ("unavailable", "current", "no_release"):
            main.present_update_result({"manual": False, "status": status}, tray, [])
        tray.showMessage.assert_not_called()
        main.present_update_result({"manual": False, "status": "available",
                                    "message": "새 버전"}, tray, [])
        tray.showMessage.assert_called_once_with("션 업데이트", "새 버전")

    def test_manual_update_asks_before_opening_official_page(self):
        tray = MagicMock()
        dialogs = []
        fake_box = MagicMock()
        fake_type = MagicMock()
        fake_type.return_value = fake_box
        fake_type.Icon = main.QMessageBox.Icon
        fake_type.StandardButton = main.QMessageBox.StandardButton
        official = "https://github.com/Logic-k/SyeonPet/releases/tag/v1.2.3"
        with patch.object(main, "QMessageBox", fake_type), \
                patch.object(main.QDesktopServices, "openUrl") as open_url:
            main.present_update_result({"manual": True, "status": "available",
                                        "message": "새 버전", "release_url": official}, tray, dialogs)
            open_url.assert_not_called()
            self.assertEqual(dialogs, [fake_box])
            handler = fake_box.finished.connect.call_args_list[0].args[0]
            handler(fake_type.StandardButton.No)
            open_url.assert_not_called()
            handler(fake_type.StandardButton.Yes)
            self.assertEqual(open_url.call_args.args[0].toString(), official)
            fake_box.open.assert_called_once()

    def test_manual_update_never_opens_a_nonofficial_url(self):
        tray = MagicMock()
        fake_box = MagicMock()
        fake_type = MagicMock(return_value=fake_box)
        fake_type.Icon = main.QMessageBox.Icon
        fake_type.StandardButton = main.QMessageBox.StandardButton
        with patch.object(main, "QMessageBox", fake_type), \
                patch.object(main.QDesktopServices, "openUrl") as open_url:
            main.present_update_result({"manual": True, "status": "available",
                                        "release_url": "https://github.com.evil.test/release"}, tray, [])
            # The only finished callback removes the dialog; there is no open callback.
            self.assertEqual(fake_box.finished.connect.call_count, 1)
            open_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
