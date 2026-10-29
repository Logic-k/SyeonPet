"""Opt-in real Windows UI smoke with synthetic data and no network.

Uses the real pet entrypoint. No files, preferences or user text are changed;
a screenshot of synthetic content is written under the system temporary folder.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(APP_DIR / "Lib" / "site-packages"))

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

import main as pet_main
import optimizer_chat
from chat import ChatPanel


def run():
    if sys.platform != "win32":
        raise RuntimeError("This smoke test requires a real Windows Qt session")
    captures = {}
    blocked = []
    test_original = "회의록을 정리해 주세요. 반드시 3개 항목을 유지하세요."
    test_revision = "표 형식으로 간결하게 작성해 주세요."
    user_edit = "사용자가 직접 추가한 시험 조건"
    image_path = Path(tempfile.mkdtemp(prefix="syeon-conversation-smoke-")) / "synthetic-panel.png"

    def blocked_operation(*_args, **_kwargs):
        blocked.append("network or child process attempted")
        raise AssertionError(blocked[-1])

    class CapturedTray(QSystemTrayIcon):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captures["tray"] = self

    class CapturedChat(ChatPanel):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captures["chat"] = self

    def fail(exc):
        traceback.print_exc()
        captures["failure"] = str(exc)
        QApplication.instance().exit(1)

    def wait_until(predicate, callback, deadline):
        try:
            if predicate():
                callback()
            elif time.monotonic() >= deadline:
                raise TimeoutError("비동기 정리 결과가 시간 내에 도착하지 않았습니다")
            else:
                QTimer.singleShot(30, lambda: wait_until(predicate, callback, deadline))
        except Exception as exc:
            fail(exc)

    def check_followup():
        p = captures["chat"].prompt_conversation
        assert p.original == test_original
        assert p.revisions == [test_revision]
        second = p.result.toPlainText()
        assert test_original in second and test_revision in second
        assert p.result_choice.count() == 2
        assert test_original in p.conversation.toPlainText()
        assert test_revision in p.conversation.toPlainText()
        p.result_choice.setCurrentIndex(0)
        assert p.result.toPlainText().endswith(user_edit), "first result edit was not retained"
        p.result_choice.setCurrentIndex(1)
        assert p.result.toPlainText() == second
        assert captures["chat"].isVisible()
        assert captures["heartbeats"] > 0, "Qt event loop was unresponsive"
        assert not blocked, "a process or network operation was attempted"
        if not captures["chat"].grab().save(str(image_path)):
            raise RuntimeError("합성 데이터 화면 캡처에 실패했습니다")
        print("PASS: real tray action opened prompt conversation in the pet window")
        print("PASS: offline initial request and follow-up retained the original and revision")
        print("PASS: editable first result survived switching and Qt event loop remained responsive")
        print("PASS: process-launch and network guards were not triggered")
        print(f"SCREENSHOT: {image_path}")
        print("LIMIT: this does not test external AI quality, backup or separate process behavior")
        QApplication.instance().quit()

    def check_first():
        p = captures["chat"].prompt_conversation
        assert p.original == test_original
        first = p.result.toPlainText()
        assert test_original in first
        p.result.setPlainText(first + "\n" + user_edit)
        p.input.setPlainText(test_revision)
        p.send.click()
        wait_until(lambda: len(p.results) == 2 and not p._busy,
                   check_followup, time.monotonic() + 5)

    def start():
        try:
            actions = [a for a in captures["tray"].contextMenu().actions()
                       if a.text() == "프롬프트 정리 열기"]
            assert len(actions) == 1
            actions[0].trigger()
            chat = captures["chat"]
            p = chat.prompt_conversation
            assert chat.isVisible() and chat.tabs.currentWidget() is p
            assert p.provider.currentData() == "offline"
            p.input.setPlainText(test_original)
            p.send.click()
            wait_until(lambda: len(p.results) == 1 and not p._busy,
                       check_first, time.monotonic() + 5)
        except Exception as exc:
            fail(exc)

    class CapturedApplication(QApplication):
        def __init__(self, args):
            super().__init__(args)
            captures["heartbeats"] = 0
            self._test_timer = QTimer(self)
            self._test_timer.timeout.connect(
                lambda: captures.__setitem__("heartbeats", captures["heartbeats"] + 1))
            self._test_timer.start(30)
            QTimer.singleShot(500, start)
            QTimer.singleShot(15000, lambda: self.exit(2))

    pet_main.QApplication = CapturedApplication
    pet_main.QSystemTrayIcon = CapturedTray
    pet_main.ChatPanel = CapturedChat
    pet_main.load_config = lambda: {"start_onboarding_on_launch": False, "ambient_chatter": False}
    pet_main.autostart_enabled = lambda: False
    sys.argv = [sys.argv[0]]
    with patch.object(QProcess, "startDetached", side_effect=blocked_operation), \
         patch.object(QProcess, "start", side_effect=blocked_operation), \
         patch.object(subprocess, "Popen", side_effect=blocked_operation), \
         patch.object(os, "startfile", side_effect=blocked_operation), \
         patch.object(socket, "create_connection", side_effect=blocked_operation), \
         patch.object(optimizer_chat, "optimize_ai", side_effect=blocked_operation):
        return pet_main.main()


if __name__ == "__main__":
    raise SystemExit(run())
