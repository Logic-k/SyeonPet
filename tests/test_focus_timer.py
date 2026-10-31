"""Deterministic deadline and shared menu tests; no elapsed-time waits."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMenu

from focus_timer import FocusTimer
from main import add_focus_timer_menu


class FocusTimerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.now = 100.0
        self.timer = FocusTimer()
        self.timer.clock = lambda: self.now
        self.expired = []
        self.changes = []
        self.timer.expired.connect(self.expired.append)
        self.timer.changed.connect(lambda: self.changes.append(self.timer.remaining_seconds))

    def tearDown(self):
        self.timer.cancel()
        self.timer.deleteLater()
        self.app.processEvents()

    def test_deadline_uses_elapsed_clock_not_number_of_ticks(self):
        self.assertEqual(self.timer.objectName(), "focusTimer")
        self.timer.start(25, "집중")
        self.assertEqual(self.timer.remaining_seconds, 1500)
        self.now += 93.25
        self.timer.check()
        self.assertEqual(self.timer.remaining_seconds, 1407)
        self.now += 1406.75
        self.timer.check()
        self.assertEqual(self.expired, ["집중"])
        self.assertEqual(self.timer.remaining_seconds, 0)
        self.assertFalse(self.timer._timer.isActive())
        self.assertGreaterEqual(len(self.changes), 3)

    def test_expiry_is_once_even_after_resume_and_repeated_check(self):
        self.timer.start(5, "휴식")
        self.now += 20000
        for _ in range(3):
            self.timer.check()
        self.assertEqual(self.expired, ["휴식"])
        self.assertEqual(self.timer.label, "")

    def test_cancel_invalidates_old_expiry(self):
        self.timer.start(25, "집중")
        self.timer.cancel()
        self.now += 2000
        self.timer.check()
        self.assertEqual(self.expired, [])
        self.assertEqual(self.timer.remaining_seconds, 0)
        self.assertFalse(self.timer._timer.isActive())

    def test_replacement_does_not_expire_at_previous_deadline(self):
        self.timer.start(5, "휴식")
        self.now += 100
        self.timer.start(25, "집중")
        self.now = 400
        self.timer.check()
        self.assertEqual(self.expired, [])
        self.assertEqual(self.timer.remaining_seconds, 1300)
        self.now = 1700
        self.timer.check()
        self.assertEqual(self.expired, ["집중"])

    def test_remaining_rounds_up_without_early_expiry(self):
        self.timer.start(5, "휴식")
        self.now = 399.99
        self.timer.check()
        self.assertEqual(self.timer.remaining_seconds, 1)
        self.assertEqual(self.expired, [])
        self.now = 400
        self.timer.check()
        self.assertEqual(self.expired, ["휴식"])

    def test_rejects_nonpositive_and_nonfinite_duration(self):
        for duration in (0, -1, float("nan"), float("inf")):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                self.timer.start(duration, "집중")
        self.assertEqual(self.timer.remaining_seconds, 0)
        self.assertEqual(self.expired, [])

    def test_shared_menu_starts_reports_cancels_and_expires(self):
        parent = QMenu()
        menu = add_focus_timer_menu(parent, self.timer)
        status, focus, rest, cancel = menu.actions()
        self.assertFalse(cancel.isEnabled())
        self.assertFalse(status.isEnabled())
        focus.trigger()
        self.assertEqual((self.timer.label, self.timer.remaining_seconds), ("집중", 1500))
        self.assertTrue(cancel.isEnabled())
        self.assertIn("25:00", status.text())
        self.now += 61
        self.timer.check()
        self.assertIn("23:59", status.text())
        cancel.trigger()
        self.now += 2000
        self.timer.check()
        self.assertEqual(self.expired, [])
        rest.trigger()
        self.assertEqual((self.timer.label, self.timer.remaining_seconds), ("휴식", 300))
        self.now += 300
        self.timer.check()
        self.assertEqual(self.expired, ["휴식"])
        self.assertTrue(cancel.isEnabled())
        parent.close()

    def test_expired_completion_remains_in_menu_until_cleared(self):
        parent = QMenu()
        menu = add_focus_timer_menu(parent, self.timer)
        status, _focus, _rest, clear = menu.actions()
        empty_status = status.text()
        self.timer.start(5, "QA_REST")
        cancel_text = clear.text()
        self.now += 301

        self.timer.check()
        self.timer.check()

        self.assertEqual(self.timer.completed_label, "QA_REST")
        self.assertEqual(self.timer.label, "")
        self.assertEqual(self.expired, ["QA_REST"])
        self.assertIn("QA_REST", status.text())
        self.assertNotEqual(clear.text(), cancel_text)
        self.assertTrue(clear.isEnabled())
        clear.trigger()
        self.assertEqual(self.timer.completed_label, "")
        self.assertEqual(status.text(), empty_status)
        self.assertFalse(clear.isEnabled())
        self.assertEqual(self.expired, ["QA_REST"])
        parent.close()

    def test_starting_another_timer_clears_prior_completion(self):
        self.timer.start(5, "QA_REST")
        self.now += 301
        self.timer.check()

        self.timer.start(25, "QA_FOCUS")

        self.assertEqual(self.timer.completed_label, "")
        self.assertEqual(self.timer.label, "QA_FOCUS")
        self.assertEqual(self.timer.remaining_seconds, 1500)
        self.assertTrue(self.timer._timer.isActive())

    def test_refresh_disconnects_when_menu_is_destroyed(self):
        parent = QMenu()
        add_focus_timer_menu(parent, self.timer)
        self.timer.start(5, "휴식")
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        with patch("sys.excepthook") as error:
            self.timer.cancel()
        error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
