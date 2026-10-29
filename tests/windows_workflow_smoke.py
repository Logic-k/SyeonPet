"""Opt-in Windows GUI test, synthetic inputs only, no network or settings writes."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Lib" / "site-packages"))

from PySide6.QtCore import QObject, QPoint, QProcess, QSettings, QTimer, Signal
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

import main as pet_main
import optimizer_chat
from chat import ChatPanel


def run():
    if sys.platform != "win32":
        raise RuntimeError("A real Windows Qt session is required")
    state = {"blocked": []}
    output = Path(tempfile.mkdtemp(prefix="syeon-workflow-smoke-"))
    prompt_draft = "합성 업무 요청 초안"
    result_draft = "합성 편집 결과"

    def blocked(*_args, **_kwargs):
        state["blocked"].append("network or process")
        raise AssertionError("Unexpected network or subprocess")

    class CapturedTray(QSystemTrayIcon):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            state["tray"] = self

    class CapturedChat(ChatPanel):
        def __init__(self, *args, **kwargs):
            kwargs["settings"] = QSettings(str(output / "settings.ini"), QSettings.Format.IniFormat)
            super().__init__(*args, **kwargs)
            state["chat"] = self

    class NoNetworkUpdates(QObject):
        completed = Signal(object)

        def check(self, manual=False):
            pass

    def fail(exc):
        traceback.print_exc()
        state["failure"] = str(exc)
        QApplication.instance().exit(1)

    def find_action(label):
        found = [a for a in state["tray"].contextMenu().actions() if a.text() == label]
        assert len(found) == 1, f"missing or duplicate tray action: {label}"
        return found[0]

    def wait_until(predicate, callback, deadline):
        try:
            if predicate():
                callback()
            elif time.monotonic() >= deadline:
                raise TimeoutError("offline result did not arrive")
            else:
                QTimer.singleShot(30, lambda: wait_until(predicate, callback, deadline))
        except Exception as exc:
            fail(exc)

    def complete():
        try:
            chat = state["chat"]
            p = chat.prompt_conversation
            assert p.original == state["selected"]
            assert state["selected"] in p.result.toPlainText()
            assert p.results[0] == result_draft
            assert p.result_choice.itemText(0) == "직접 작성한 결과"
            assert state.get("thinking_started") == ("thinking", "thinking")
            assert len(chat.pet.sprites.states["thinking"]["frames"]) == 6
            assert state.get("thinking_capture"), "thinking capture failed"
            assert chat.behavior.mode != "thinking", "thinking state did not finish"
            assert not state["blocked"]
            if not chat.grab().save(str(output / "result.png")):
                raise RuntimeError("result capture failed")
            print("PASS: tray action opened in-app prompt conversation")
            print("PASS: full sleep blocked sends while preserving drafts and results; explicit wake restored them")
            print("PASS: full sleep froze the sprite and frame blend toggle enabled and disabled")
            print("PASS: office category filter, template selection and offline prompt creation")
            print("PASS: pet right-click opened the shared tray menu and opacity remained adjustable")
            print("PASS: six thinking frames loaded, busy state used thinking and later restored")
            print("PASS: no network or separate process was used")
            print("SCREENSHOTS:", output)
            print("LIMIT: Qt action invocation, not mouse hit-testing or multi-monitor/real AI validation")
            QApplication.instance().quit()
        except Exception as exc:
            fail(exc)

    def exercise():
        try:
            find_action("프롬프트 정리 열기").trigger()
            chat = state["chat"]
            p = chat.prompt_conversation
            assert chat.isVisible() and p.isVisible() and not hasattr(chat, "tabs")
            assert p.provider.currentData() == "offline"
            p.input.setPlainText(prompt_draft)
            p.result.setPlainText(result_draft)
            find_action("완전히 가만히 재우기").trigger()
            assert chat.behavior.fully_asleep
            assert not p.send.isEnabled()
            assert chat.wake_button.isVisible()
            p.submit()
            assert p.original == ""
            assert p.input.toPlainText() == prompt_draft
            assert p.result.toPlainText() == result_draft
            pet = chat.pet
            index = pet.frame_idx
            pet._advance_frame(3000)
            assert pet.frame_idx == index and pet._proc_fx() == (1.0, 1.0, 0.0, 0.0, 0.0)
            blend = find_action("프레임 부드럽게 전환")
            blend.trigger()
            assert pet.crossfade and pet.BLEND_MS > 0
            blend.trigger()
            assert not pet.crossfade
            # Send a real Qt context-menu event to the pet and verify the
            # same menu instance used by its tray, without starting a nested loop.
            local = QPoint(pet.width() // 2, pet.height() // 2)
            event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, local, pet.mapToGlobal(local))
            QApplication.sendEvent(pet, event)
            QApplication.processEvents()
            menu = state["tray"].contextMenu()
            assert menu.isVisible() and event.isAccepted()
            menu.hide()
            chat.opacity_slider.setValue(80)
            assert abs(chat.windowOpacity() - 0.8) < 0.02
            # Persist only in the isolated temporary INI and verify a new
            # panel picks it up; do not touch the actual user's settings.
            chat._settings.sync()
            other = ChatPanel(pet, chat.behavior, {}, settings=QSettings(
                str(output / "settings.ini"), QSettings.Format.IniFormat))
            assert other.opacity_slider.value() == 80
            other.close()
            chat.opacity_slider.setValue(100)
            # A closed panel is hidden, not destroyed; draft and result stay.
            chat.close()
            assert not chat.isVisible()
            chat.show_near_pet()
            assert p.input.toPlainText() == prompt_draft
            assert p.result.toPlainText() == result_draft
            quit_called = []
            chat.request_app_quit(lambda: quit_called.append(True))
            assert chat._quit_dialog is not None
            chat._quit_dialog.done(QMessageBox.StandardButton.No)
            QApplication.processEvents()
            assert not quit_called and p.input.toPlainText() == prompt_draft
            QApplication.processEvents()
            if not chat.grab().save(str(output / "sleep.png")):
                raise RuntimeError("sleep capture failed")
            chat.wake_button.click()
            assert not chat.behavior.fully_asleep and p.send.isEnabled()
            assert p.input.toPlainText() == prompt_draft
            assert p.result.toPlainText() == result_draft
            p.input.clear()
            p.template.click()
            picker = p._template_dialog
            assert picker.isVisible()
            picker.office_button.click()
            assert picker.office_button.isChecked() and picker.list.count() >= 15
            picker.list.setCurrentRow(0)
            selected = picker.preview.toPlainText()
            assert selected.strip() and "[" in selected
            QApplication.processEvents()
            if not picker.grab().save(str(output / "templates.png")):
                raise RuntimeError("template capture failed")
            picker.select_button.click()
            assert p.input.toPlainText() == selected
            state["selected"] = selected
            def record_busy(busy):
                if busy:
                    state["thinking_started"] = (chat.behavior.mode, pet.state)
                    state["thinking_capture"] = pet.grab().save(str(output / "thinking.png"))
            p.busy_changed.connect(record_busy)
            p.send.click()
            wait_until(lambda: len(p.results) == 2 and not p._busy,
                       lambda: wait_until(lambda: chat.behavior.mode != "thinking",
                                          complete, time.monotonic() + 3),
                       time.monotonic() + 5)
        except Exception as exc:
            fail(exc)

    class CapturedApplication(QApplication):
        def __init__(self, args):
            super().__init__(args)
            QTimer.singleShot(500, exercise)
            QTimer.singleShot(15000, lambda: self.exit(2))

    pet_main.QApplication = CapturedApplication
    pet_main.QSystemTrayIcon = CapturedTray
    pet_main.ChatPanel = CapturedChat
    pet_main.UpdateChecker = NoNetworkUpdates
    pet_main.load_config = lambda: {"start_onboarding_on_launch": False, "ambient_chatter": False}
    pet_main.autostart_enabled = lambda: False
    sys.argv = [sys.argv[0]]
    with patch.object(QProcess, "startDetached", side_effect=blocked), \
         patch.object(QProcess, "start", side_effect=blocked), \
         patch.object(subprocess, "Popen", side_effect=blocked), \
         patch.object(os, "startfile", side_effect=blocked), \
         patch.object(socket, "create_connection", side_effect=blocked), \
         patch.object(optimizer_chat, "optimize_ai", side_effect=blocked):
        return pet_main.main()


if __name__ == "__main__":
    raise SystemExit(run())
