"""Interactive Windows acceptance fixture for the real SyeonPet main UI.

Runs the single prompt-conversation UI with isolated temporary preferences,
without writing to user settings or modifying the library. Normal mode blocks network.
With --allow-vireonix, only the fixed synthetic test sentence may be sent to
Vireonix after the regular UI confirmation. Exits after four minutes.
"""
from __future__ import annotations

from contextlib import ExitStack
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Lib" / "site-packages"))

from PySide6.QtCore import QObject, QProcess, QSettings, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication

import main
import optimizer_chat
import wardrobe
from chat import ChatPanel
from pet import PetWidget


def blocked(*_args, **_kwargs):
    raise RuntimeError("Acceptance fixture blocks external requests and processes")


SYNTHETIC_PROMPT = "초등학생이 이해하기 쉬운 광합성 설명을 세 문장으로 요청하는 프롬프트를 작성해 주세요."
real_optimize = optimizer_chat.optimize_ai


def synthetic_vireonix_only(prompt, **kwargs):
    if (prompt != SYNTHETIC_PROMPT or kwargs.get("provider") != "vireonix"
            or kwargs.get("context") or kwargs.get("revision") or kwargs.get("previous_result")):
        raise RuntimeError("QA permits only its fixed synthetic Vireonix request")
    return real_optimize(prompt, **kwargs)


class AcceptancePet(PetWidget):
    def __init__(self, *args, **kwargs):
        settings = QSettings(str(Path(test_dir) / "wardrobe.ini"), QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        kwargs["wardrobe_settings"] = settings
        super().__init__(*args, **kwargs)
        self.setWindowTitle("Syeon Pet QA")
        # Expose this test-only pet to Windows UI Automation. Production uses
        # Tool rather than Window; context menu and mouse code are unchanged.
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)


class AcceptanceChat(ChatPanel):
    def __init__(self, *args, **kwargs):
        settings = QSettings(str(Path(test_dir) / "settings.ini"), QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        kwargs["settings"] = settings
        super().__init__(*args, **kwargs)
        self.setWindowTitle("Syeon Acceptance QA")
        # Expose the test window to Windows UI automation; production keeps Tool.
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        # Hold this independent synthetic pet still while the pointer is used.
        self.behavior.timer.stop()
        self.behavior.idle(300)
        QTimer.singleShot(800, self.show_near_pet)


class NoNetworkUpdates(QObject):
    completed = Signal(object)

    def __init__(self, *_args, **_kwargs):
        super().__init__()

    def check(self, manual=False):
        pass

    def begin_shutdown(self):
        pass

    def cancel_shutdown(self):
        pass


class AcceptanceApp(QApplication):
    def __init__(self, args):
        super().__init__(args)
        QTimer.singleShot(240000, self.quit)


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Windows required")
    main.QApplication = AcceptanceApp
    main.ChatPanel = AcceptanceChat
    main.PetWidget = AcceptancePet
    main.UpdatePreparer = NoNetworkUpdates
    main.load_config = lambda: {
        "start_onboarding_on_launch": False,
        "ambient_chatter": False,
    }
    main.autostart_enabled = lambda: False
    allow_vireonix = sys.argv[1:] == ["--allow-vireonix"]
    sys.argv = [sys.argv[0]]
    with tempfile.TemporaryDirectory(prefix="syeon-acceptance-") as test_dir, ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"APPDATA": str(Path(test_dir) / "appdata"),
                                                    "LOCALAPPDATA": str(Path(test_dir) / "localappdata")}))
        stack.enter_context(patch.object(wardrobe, "pack_root", return_value=Path(test_dir) / "packs"))
        stack.enter_context(patch("pet.pack_root", return_value=Path(test_dir) / "packs"))
        stack.enter_context(patch.object(QApplication, "clipboard", side_effect=blocked))
        stack.enter_context(patch.object(QProcess, "startDetached", side_effect=blocked))
        stack.enter_context(patch.object(subprocess, "Popen", side_effect=blocked))
        stack.enter_context(patch.object(optimizer_chat, "optimize_ai", side_effect=(
            synthetic_vireonix_only if allow_vireonix else blocked)))
        if not allow_vireonix:
            stack.enter_context(patch.object(socket, "create_connection", side_effect=blocked))
        raise SystemExit(main.main())
