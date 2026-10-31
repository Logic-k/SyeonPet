"""Local companion feedback, bubble ownership and persistent pet controls."""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings, QRect
from PySide6.QtWidgets import QApplication

import main
from behavior import BehaviorController
from bubble import SpeechBubble
from chat import ChatPanel
from focus_timer import FocusTimer
from pet import ASSETS, PetWidget, SpriteBank


class CompanionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.sprites = SpriteBank(ASSETS)

    def setUp(self):
        self.pet = PetWidget(self.sprites)
        self.pet.tick.stop()
        self.behavior = BehaviorController(self.pet)
        self.behavior.timer.stop()
        self.bubble = SpeechBubble(self.pet)
        self.pet.bubble_widget = self.bubble
        self.callbacks = []
        self.shots = patch("bubble.QTimer.singleShot", side_effect=lambda ms, callback: self.callbacks.append(callback))
        self.shots.start()

    def tearDown(self):
        self.shots.stop()
        self.pet.close()
        self.pet.deleteLater()
        self.app.processEvents()

    def feedback(self, outcome="success", enabled=True):
        return main.task_feedback(self.behavior, self.bubble, enabled, outcome)

    def test_all_outcomes_show_a_short_local_bubble_without_changing_pose(self):
        original = (self.behavior.mode, self.pet.state)
        for outcome in ("success", "failure", "copied"):
            with self.subTest(outcome=outcome):
                self.assertTrue(self.feedback(outcome))
                self.assertFalse(self.bubble.isHidden())
                self.assertTrue(self.bubble.btn_next.isHidden())
                self.assertEqual((self.behavior.mode, self.pet.state), original)
        self.bubble.hide()
        self.assertFalse(self.feedback("PRIVATE_PROMPT_CONTENT"))
        self.assertTrue(self.bubble.isHidden())

    def test_quiet_sleep_tour_drag_fall_and_manual_actions_block_feedback(self):
        self.assertFalse(self.feedback(enabled=False))
        for action in (self.behavior.sleep, self.behavior.deep_sleep,
                       self.behavior.still_sleep, self.behavior.dance,
                       lambda: self.behavior.play_one_shot("wave")):
            with self.subTest(action=action):
                action()
                original = (self.behavior.mode, self.pet.state, self.behavior.fully_asleep)
                for outcome in ("success", "failure", "copied"):
                    self.assertFalse(self.feedback(outcome))
                self.assertEqual((self.behavior.mode, self.pet.state, self.behavior.fully_asleep), original)
        self.behavior.idle()
        self.behavior.onboarding_active = True
        self.assertFalse(self.feedback())
        self.behavior.onboarding_active = False
        self.pet._dragging = True
        self.assertFalse(self.feedback())
        self.pet._dragging = False
        self.behavior._fell = True
        self.assertFalse(self.feedback())
        self.behavior._fell = False
        self.behavior.mode = "fall"
        self.assertFalse(self.feedback())
        self.assertTrue(self.bubble.isHidden())

    def test_feedback_keeps_thinking_snapshot_and_restores_walk(self):
        self.behavior.wander()
        if self.pet.sprites.has("turn_bridge"):
            self.assertEqual(self.pet.state, "turn_bridge")
            self.pet._advance_frame(sum(self.pet._animation_meta()["durations"]))
            self.behavior._tick()
        target = self.behavior.target_x
        self.behavior.begin_thinking()
        snapshot = self.behavior._thinking_restore
        self.assertTrue(self.feedback())
        self.assertIs(self.behavior._thinking_restore, snapshot)
        self.behavior._finish_thinking()
        self.assertEqual((self.behavior.mode, self.pet.state), ("walk", "walk"))
        self.assertEqual(self.behavior.target_x, target)

    def test_interrupted_thinking_and_saved_manual_action_are_not_overridden(self):
        self.behavior.play_one_shot("wave")
        self.behavior.begin_thinking()
        self.assertFalse(self.feedback())
        self.behavior.still_sleep()
        self.assertFalse(self.feedback())
        self.behavior.end_thinking()
        self.assertEqual((self.pet.state, self.behavior.fully_asleep), ("still", True))

    def test_stale_ambient_hide_cannot_erase_feedback_or_tour(self):
        self.bubble.show_temporary("ambient")
        old_hide = self.callbacks[-1]
        self.assertTrue(self.feedback("copied"))
        feedback_hide = self.callbacks[-1]
        old_hide()
        self.assertFalse(self.bubble.isHidden())
        self.bubble.show_step("tour", 0, 2, True)
        feedback_hide()
        self.assertFalse(self.bubble.isHidden())
        self.assertEqual(self.bubble.text.text(), "tour")
        self.assertFalse(self.bubble.btn_next.isHidden())

    def test_current_hide_works_and_hide_invalidates_an_old_callback(self):
        self.bubble.show_temporary("first")
        first = self.callbacks[-1]
        first()
        self.assertTrue(self.bubble.isHidden())
        self.bubble.show_temporary("second")
        second = self.callbacks[-1]
        self.bubble.hide()
        self.bubble.show_step("new owner", 0, 1, False)
        second()
        self.assertFalse(self.bubble.isHidden())

    def test_feedback_checks_pending_owner_even_after_pose_changed(self):
        for current in ("idle", "walk"):
            for prior in ("sleep", "still_sleep", "one_shot", "drag"):
                self.behavior.mode = current
                self.behavior._thinking_restore = {"mode": prior}
                self.assertFalse(self.feedback())
                self.assertEqual(self.behavior.mode, current)
        self.assertTrue(self.bubble.isHidden())

    def test_timer_expiry_does_not_wake_or_speak_during_full_sleep(self):
        timer = FocusTimer()
        now = [10.0]
        timer.clock = lambda: now[0]
        timer.expired.connect(lambda label: main.show_companion_message(
            self.behavior, self.bubble, True, label))
        timer.start(5, "휴식")
        self.behavior.still_sleep()
        now[0] += 301
        timer.check()
        timer.check()
        self.assertTrue(self.behavior.fully_asleep)
        self.assertEqual(self.pet.state, "still")
        self.assertTrue(self.bubble.isHidden())
        timer.deleteLater()

    def test_narrow_and_negative_work_area_always_have_valid_wander_bounds(self):
        for left, width in ((0, 1), (-200, 120), (-1000, self.pet.width() + 12),
                            (-1920, 1920), (0, 1920)):
            with self.subTest(left=left, width=width):
                area = QRect(left, 0, width, 400)
                with patch.object(self.pet, "screen_bounds", return_value=area), \
                        patch("behavior.random.randint", wraps=__import__("random").randint) as choose:
                    self.behavior.wander()
                low, high = choose.call_args.args
                self.assertLessEqual(low, high)
                self.assertLessEqual(low, self.behavior.target_x)
                self.assertLessEqual(self.behavior.target_x, high)
                if width >= self.pet.width() + 21:
                    self.assertEqual((low, high), (left + 10, area.right() - self.pet.width() - 10))
                else:
                    self.assertEqual(low, left)

    def test_preferences_defaults_and_invalid_restored_choices(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = QSettings(str(Path(temporary) / "pet.ini"), QSettings.Format.IniFormat)
            cfg = {"calm_mode": True, "ambient_chatter": False, "frame_blend_ms": 30,
                   "click_actions": {"double_click": "wave"}}
            self.assertEqual(main.load_pet_preferences(settings, cfg),
                             {"calm": True, "speech": False, "blend": True, "double_click": "wave",
                              "reduced_motion": False})
            settings.setValue("double_click", "not-an-action")
            settings.setValue("calm", "invalid")
            settings.setValue("speech", "true")
            self.assertEqual(main.load_pet_preferences(settings, cfg),
                             {"calm": True, "speech": True, "blend": True, "double_click": "wave",
                              "reduced_motion": False})
            cfg["click_actions"]["double_click"] = "invalid-config-action"
            self.assertEqual(main.load_pet_preferences(settings, cfg)["double_click"], "sleep")

    def test_runtime_signal_timer_and_preference_restart_integration(self):
        # Exercise real main wiring, pet, menu, prompt and QSettings. Only
        # external update/draft surfaces and the outer event loop are isolated.
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "pet.ini")
            cfg = {"ambient_lines": [], "ambient_chatter": True}
            before_config = main.CONFIG_PATH.read_bytes()
            pets, behaviors, timers, panels = [], [], [], []
            tray = MagicMock()
            def make_pet(*args, **kwargs):
                value = PetWidget(*args, **kwargs)
                value.tick.stop()
                pets.append(value)
                return value
            def make_behavior(pet):
                value = BehaviorController(pet)
                value.timer.stop()
                behaviors.append(value)
                return value
            def make_timer(parent):
                value = FocusTimer(parent)
                value.clock = lambda: 100.0
                timers.append(value)
                return value
            def make_panel(*args):
                value = ChatPanel(*args)
                panels.append(value)
                return value
            def exercise(first):
                menu = tray.setContextMenu.call_args.args[0]
                actions = {action.text(): action for action in menu.actions()}
                status = next(action for action in menu.actions()
                              if isinstance(action, main.QWidgetAction))
                self.assertEqual(status.text(), status.defaultWidget().text())
                pet, behavior, timer = pets[-1], behaviors[-1], timers[-1]
                bubble = pet.bubble_widget
                self.assertIs(self.app.findChild(FocusTimer, "focusTimer"), timer)
                prompt = panels[-1].prompt_conversation
                if first:
                    prompt.task_feedback.emit("success")
                    self.assertFalse(bubble.isHidden())
                    actions["차분 모드 (천천히)"].trigger()
                    actions["프레임 부드럽게 전환"].trigger()
                    double = actions["더블클릭 동작"].menu()
                    next(action for action in double.actions() if action.text() == "점프하기").trigger()
                    actions["말풍선 표시"].trigger()
                    self.assertTrue(bubble.isHidden())
                    prompt.task_feedback.emit("failure")
                    self.assertTrue(bubble.isHidden())
                    timer.start(5, "휴식")
                    timer.clock = lambda: 401.0
                    timer.check()
                    self.assertTrue(bubble.isHidden())
                    actions["고요 모드 (작업 계속)"].trigger()
                    self.assertTrue(pet.reduced_motion)
                    self.assertFalse(behavior.fully_asleep)
                    self.assertFalse(pet.tick.isActive())
                else:
                    self.assertTrue(actions["차분 모드 (천천히)"].isChecked())
                    self.assertFalse(actions["말풍선 표시"].isChecked())
                    self.assertTrue(actions["프레임 부드럽게 전환"].isChecked())
                    self.assertTrue(behavior.slow)
                    self.assertTrue(pet.crossfade)
                    self.assertGreater(pet.BLEND_MS, 0)
                    self.assertEqual(behavior.click_actions["double_click"], "jump")
                    self.assertTrue(actions["고요 모드 (작업 계속)"].isChecked())
                    self.assertTrue(pet.reduced_motion)
                    policy = policies[-1]
                    policy.query = lambda: True
                    policy.refresh()
                    actions["고요 모드 (작업 계속)"].trigger()
                    self.assertFalse(actions["고요 모드 (작업 계속)"].isChecked())
                    self.assertTrue(pet.reduced_motion)
                    policy.query = lambda: False
                    policy.refresh()
                    self.assertFalse(pet.reduced_motion)
                    self.assertTrue(actions["프레임 부드럽게 전환"].isEnabled())
                    self.assertTrue(pet.crossfade)
                return 0
            from motion_policy import MotionPreference
            policies = []
            def make_policy(app):
                value = MotionPreference(app, query=lambda: False)
                policies.append(value)
                return value
            with ExitStack() as stack:
                stack.enter_context(patch.object(main, "QApplication", return_value=self.app))
                stack.enter_context(patch.object(main, "load_config", return_value=cfg))
                stack.enter_context(patch.object(main, "QSettings", side_effect=lambda *args: QSettings(path, QSettings.Format.IniFormat)))
                stack.enter_context(patch.object(main, "PetWidget", side_effect=make_pet))
                stack.enter_context(patch.object(main, "BehaviorController", side_effect=make_behavior))
                stack.enter_context(patch.object(main, "MotionPreference", side_effect=make_policy))
                stack.enter_context(patch.object(main, "FocusTimer", side_effect=make_timer))
                stack.enter_context(patch.object(main, "ChatPanel", side_effect=make_panel))
                stack.enter_context(patch.object(main, "QSystemTrayIcon", return_value=tray))
                stack.enter_context(patch.object(main, "UpdatePreparer"))
                drafts = stack.enter_context(patch.object(main, "RestartDrafts"))
                drafts.return_value.restore.return_value = False
                drafts.return_value.remaining_count.return_value = 0
                stack.enter_context(patch.object(main, "autostart_enabled", return_value=False))
                autostart = stack.enter_context(patch.object(main, "set_autostart"))
                for first in (True, False):
                    with patch.object(self.app, "exec", side_effect=lambda: exercise(first)):
                        self.assertEqual(main.main(), 0)
                    timers[-1].cancel()
                    pets[-1].tick.stop()
                    timers[-1].setParent(None)
                    timers[-1].deleteLater()
                    self.app.closeAllWindows()
                autostart.assert_not_called()
            self.assertEqual(main.CONFIG_PATH.read_bytes(), before_config)
            settings = QSettings(path, QSettings.Format.IniFormat)
            self.assertEqual(main.load_pet_preferences(settings, cfg),
                             {"calm": True, "speech": False, "blend": True, "double_click": "jump",
                              "reduced_motion": False})


if __name__ == "__main__":
    unittest.main()
