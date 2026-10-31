"""Policy changes keep the assistant awake, controllable and reversible."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QByteArray, QCoreApplication, QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QApplication

from behavior import BehaviorController
from bubble import SpeechBubble
from motion_policy import MotionPreference, WM_SETTINGCHANGE, read_windows_reduced_motion
from onboarding import OnboardingTour
from pet import PetWidget
from test_pet_motion import FakeBank
from wardrobe import WardrobeWindow


class ReducedMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        settings = QSettings(str(Path(self.temporary.name) / "wardrobe.ini"),
                             QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        self.pet = PetWidget(FakeBank(), display_size=32, wardrobe_settings=settings)
        self.pet.tick.stop()
        self.behavior = BehaviorController(self.pet)
        self.behavior.timer.stop()
        self.policies = []
        self.windows = []
        self.tours = []

    def tearDown(self):
        for policy in self.policies:
            policy.stop()
            policy.deleteLater()
        for tour in self.tours:
            tour._tick.stop()
            tour.deleteLater()
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.pet.tick.stop()
        self.behavior.timer.stop()
        self.behavior.deleteLater()
        self.pet.close()
        self.pet.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temporary.cleanup()

    def policy(self, value):
        policy = MotionPreference(self.app, query=lambda: value)
        self.policies.append(policy)
        return policy

    def tour(self):
        bubble = SpeechBubble(self.pet)
        self.pet.bubble_widget = bubble
        self.pet.show()
        tour = OnboardingTour(self.pet, self.behavior, bubble,
                              [{"text": "QA_FIRST"}, {"text": "QA_SECOND"}])
        self.tours.append(tour)
        return tour

    def wardrobe(self):
        with patch("wardrobe.pack_root", return_value=Path(self.temporary.name) / "packs"):
            window = WardrobeWindow(self.pet)
        self.windows.append(window)
        return window

    def test_unknown_initial_query_does_not_force_reduction(self):
        policy = self.policy(None)
        self.assertFalse(policy.known)
        self.assertFalse(policy.reduced)

    def test_windows_disabled_animation_output_requests_reduction(self):
        def disabled(_action, _size, value, _flags):
            value._obj.value = False
            return 1

        with patch("motion_policy.ctypes.windll.user32.SystemParametersInfoW",
                   side_effect=disabled) as query:
            reduced = read_windows_reduced_motion()

        self.assertIs(reduced, True)
        self.assertEqual(query.call_args.args[0], 0x1042)

    def test_windows_enabled_animation_output_keeps_motion(self):
        def enabled(_action, _size, value, _flags):
            value._obj.value = True
            return 1

        with patch("motion_policy.ctypes.windll.user32.SystemParametersInfoW",
                   side_effect=enabled):
            reduced = read_windows_reduced_motion()

        self.assertIs(reduced, False)

    def test_failed_windows_query_is_not_treated_as_disabled_animation(self):
        with patch("motion_policy.ctypes.windll.user32.SystemParametersInfoW", return_value=0):
            reduced = read_windows_reduced_motion()

        self.assertIsNone(reduced)

    def test_query_failure_preserves_last_known_reduction(self):
        policy = self.policy(True)
        policy.query = lambda: None

        policy.refresh()

        self.assertTrue(policy.known)
        self.assertTrue(policy.reduced)

    def test_native_settings_message_refreshes_without_consuming_event(self):
        policy = self.policy(False)
        changes = []
        policy.changed.connect(changes.append)
        policy.query = lambda: True
        message = wintypes.MSG()
        message.message = WM_SETTINGCHANGE

        consumed = policy._filter.nativeEventFilter(
            QByteArray(b"windows_generic_MSG"), ctypes.addressof(message))

        self.assertFalse(consumed)
        self.assertTrue(policy.reduced)
        self.assertEqual(changes, [True])

    def test_unrelated_native_event_does_not_query_or_dereference(self):
        policy = self.policy(False)
        query = Mock(return_value=True)
        policy.query = query

        consumed = policy._filter.nativeEventFilter(QByteArray(b"xcb_generic_event_t"), 0)

        self.assertFalse(consumed)
        query.assert_not_called()

    def test_reduction_stops_existing_walk_without_sleep_or_position_jump(self):
        self.behavior.wander()
        position = self.pet.pos()
        self.pet.set_reduced_motion(True)

        self.behavior._tick()

        self.assertEqual(self.behavior.mode, "idle")
        self.assertEqual(self.pet.pos(), position)
        self.assertFalse(self.behavior.fully_asleep)

    def test_reduced_idle_does_not_start_autonomous_motion(self):
        self.pet.set_reduced_motion(True)
        self.behavior.mode_until = 0

        self.behavior._tick()

        self.assertEqual(self.behavior.mode, "idle")
        self.assertFalse(self.behavior.fully_asleep)

    def test_reduced_direct_decorative_action_does_not_enter_frozen_one_shot(self):
        self.pet.set_reduced_motion(True)

        self.behavior.play_one_shot("wave")

        self.assertEqual(self.behavior.mode, "idle")
        self.assertEqual(self.pet.state, "idle")

    def test_static_thinking_completes_without_becoming_asleep(self):
        self.pet.set_reduced_motion(True)
        self.behavior.begin_thinking()
        self.behavior._thinking_started -= 1

        self.behavior.end_thinking()

        self.assertIsNone(self.behavior._thinking_restore)
        self.assertEqual(self.behavior.mode, "idle")
        self.assertFalse(self.behavior.fully_asleep)

    def test_reduction_during_thinking_does_not_resume_translation(self):
        self.behavior.wander()
        self.behavior.begin_thinking()
        position = self.pet.pos()
        self.pet.set_reduced_motion(True)
        self.behavior._thinking_started -= 1

        self.behavior.end_thinking()
        self.behavior._tick()

        self.assertEqual(self.pet.pos(), position)
        self.assertEqual(self.behavior.mode, "idle")
        self.assertIsNone(self.behavior._thinking_restore)

    def test_reduced_drag_release_preserves_full_sleep_without_physics(self):
        self.behavior.still_sleep()
        self.pet.set_reduced_motion(True)
        self.pet._dragging = True
        self.behavior._on_drag_started()
        self.pet._dragging = False

        with patch.object(self.pet, "physics_step", side_effect=AssertionError("unexpected physics")):
            self.behavior._tick()

        self.assertTrue(self.behavior.fully_asleep)
        self.assertEqual(self.pet.state, "still")
        self.assertFalse(self.behavior._fell)

    def test_reduced_tour_is_stationary_and_navigation_stays_available(self):
        self.pet.set_reduced_motion(True)
        tour = self.tour()
        position = self.pet.sprite_rect_global()
        tour.start()

        tour.next()
        tour._update()

        self.assertEqual(tour.index, 1)
        self.assertEqual(self.pet.sprite_rect_global(), position)
        self.assertFalse(tour._tick.isActive())

    def test_reduced_tour_navigation_preserves_dragged_sprite_position(self):
        self.pet.set_reduced_motion(True)
        tour = self.tour()
        tour.start()
        start = self.pet.sprite_rect_global().center()
        end = start - QPoint(0, 180)
        for kind, global_pos, button, buttons in (
                (QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
                (QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
                (QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)):
            event = QMouseEvent(kind, QPointF(self.pet.mapFromGlobal(global_pos)),
                                QPointF(global_pos), button, buttons, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(self.pet, event)
        self.behavior._tick()
        position = self.pet.sprite_rect_global()

        tour.next()
        self.assertEqual(self.pet.sprite_rect_global(), position)
        tour.prev()
        self.assertEqual(self.pet.sprite_rect_global(), position)
        self.assertFalse(tour._tick.isActive())

    def test_policy_transition_keeps_tour_step(self):
        tour = self.tour()
        tour.start()
        tour.next()
        self.pet.set_reduced_motion(True)

        tour.refresh_motion_policy()

        self.assertEqual(tour.index, 1)
        self.assertFalse(tour._tick.isActive())

    def test_restored_motion_resumes_tour_at_current_step(self):
        tour = self.tour()
        self.pet.set_reduced_motion(True)
        tour.start()
        tour.next()
        self.pet.set_reduced_motion(False)

        tour.refresh_motion_policy()

        self.assertEqual(tour.index, 1)
        self.assertTrue(tour._tick.isActive())

    def test_reduction_stops_preview_but_keeps_manual_frames(self):
        window = self.wardrobe()
        window._toggle_preview()

        window.set_reduced_motion(True)

        self.assertFalse(window._preview_playing)
        self.assertFalse(window._preview_timer.isActive())
        self.assertFalse(window.preview_play.isEnabled())
        self.assertTrue(window.preview_slider.isEnabled())

    def test_reduced_preview_manual_frame_changes_rendered_image(self):
        window = self.wardrobe()
        window.set_reduced_motion(True)

        window.preview_slider.setValue(1)

        self.assertEqual(window.preview.pixmap().toImage().pixelColor(75, 75), QColor("blue"))
        self.assertFalse(window._preview_playing)

    def test_motion_selection_cannot_reenable_reduced_preview_playback(self):
        window = self.wardrobe()
        window.set_reduced_motion(True)

        window.motion_combo.setCurrentIndex(window.motion_combo.findData("wave"))

        self.assertFalse(window.preview_play.isEnabled())

    def test_restoring_motion_does_not_restart_preview_automatically(self):
        window = self.wardrobe()
        window._toggle_preview()
        window.set_reduced_motion(True)

        window.set_reduced_motion(False)

        self.assertTrue(window.preview_play.isEnabled())
        self.assertFalse(window._preview_playing)
        self.assertFalse(window._preview_timer.isActive())


if __name__ == "__main__":
    unittest.main()
