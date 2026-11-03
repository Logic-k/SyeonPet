"""Windows GUI smoke verification for SyeonPet Chat & Prompt components."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(APP_DIR / "Lib" / "site-packages"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

import main as pet_main
from optimizer_chat import PromptConversation
from syeonpet.chat.direct_work_ui import DirectWorkWidget


class WindowsSmokeTests(unittest.TestCase):
    """Smoke tests for PromptConversation and DirectWorkWidget on Windows."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_prompt_conversation_smoke(self):
        with patch("ai_profiles.load_profiles", return_value=[]):
            pc = PromptConversation()
            pc.show()
            self.app.processEvents()
            self.assertIsNotNone(pc.input)
            self.assertIsNotNone(pc.submit)
            self.assertIsNotNone(pc.provider)
            self.assertFalse(pc.is_busy)
            pc.close()
            self.app.processEvents()

    def test_direct_work_widget_smoke(self):
        dww = DirectWorkWidget()
        dww.show()
        self.app.processEvents()
        self.assertIsNotNone(dww.input)
        self.assertIsNotNone(dww.result)
        self.assertIsNotNone(dww.mode_combo)
        self.assertFalse(dww._busy)
        dww.close()
        self.app.processEvents()

    def test_main_hub_smoke(self):
        with tempfile.TemporaryDirectory(prefix="syeon-smoke-") as temp:
            captures = {"chat": None}
            from chat import ChatPanel

            class CapturedChat(ChatPanel):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    captures["chat"] = self

            with patch.object(pet_main, "ChatPanel", CapturedChat), \
                 patch.object(pet_main, "QApplication", return_value=self.app), \
                 patch.object(self.app, "exec", return_value=0), \
                 patch.object(pet_main, "UpdatePreparer"), \
                 patch.object(pet_main, "autostart_enabled", return_value=False), \
                 patch.object(pet_main, "load_config", return_value={"start_onboarding_on_launch": False, "ambient_chatter": False}):
                res = pet_main.main()
                self.assertEqual(res, 0)
                chat = captures["chat"]
                self.assertIsNotNone(chat)
                self.assertIsInstance(chat.prompt_conversation, PromptConversation)
                if hasattr(chat, "work_widget") and chat.work_widget is not None:
                    self.assertIsInstance(chat.work_widget, DirectWorkWidget)
                self.app.closeAllWindows()
                self.app.processEvents()


def run() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(WindowsSmokeTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(run())
