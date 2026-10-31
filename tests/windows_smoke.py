"""Opt-in Windows GUI smoke: python windows_smoke.py <toolkit.exe>.

This can show or focus an existing toolkit window. It does not close that
window, send text, invoke AI, or alter the user's SyeonPet settings.
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import tempfile
import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(APP_DIR / "Lib" / "site-packages"))

from PySide6.QtCore import QProcess, QSettings, QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

import main as pet_main
import prompt_toolkit
from chat import ChatPanel


def toolkit_window_visible() -> bool:
    """Check a visible native window, excluding Windows error dialogs."""
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows.argtypes = [enum_proc, ctypes.c_void_p]
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    found = []

    @enum_proc
    def inspect(hwnd, _):
        title = ctypes.create_unicode_buffer(512)
        window_class = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, len(title))
        user32.GetClassNameW(hwnd, window_class, len(window_class))
        if (title.value == "프롬프트 툴킷" and window_class.value != "#32770"
                and user32.IsWindowVisible(hwnd)):
            found.append(hwnd)
        return True

    user32.EnumWindows(inspect, 0)
    return bool(found)


def run(path: Path) -> int:
    if sys.platform != "win32" or not path.is_file() or path.suffix.lower() != ".exe":
        raise ValueError("Windows와 존재하는 툴킷 .exe 경로가 필요합니다.")

    captures = {"launches": [], "calls": [], "results": [], "errors": []}
    sentinel = "smoke-test: 이 문장은 전달하지 않습니다"

    with tempfile.TemporaryDirectory(prefix="syeon-smoke-") as temp:
        class CapturedTray(QSystemTrayIcon):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captures["tray"] = self

        class CapturedChat(ChatPanel):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captures["chat"] = self

        class CapturedLauncher(prompt_toolkit.PromptToolkitLauncher):
            def __init__(self):
                self._settings = QSettings(str(Path(temp) / "settings.ini"), QSettings.Format.IniFormat)
                self._settings.setValue("executablePath", str(path.resolve()))

            def open(self, parent=None):
                captures["calls"].append(parent)
                result = super().open(parent)
                captures["results"].append(result)
                return result

        class TrackedProcess:
            @staticmethod
            def startDetached(program, args, working_directory):
                captures["launches"].append((program, list(args), working_directory))
                return QProcess.startDetached(program, args, working_directory)

        pet_main.QSystemTrayIcon = CapturedTray
        pet_main.ChatPanel = CapturedChat
        pet_main.PromptToolkitLauncher = CapturedLauncher
        prompt_toolkit.QProcess = TrackedProcess
        pet_main.load_config = lambda: {"start_onboarding_on_launch": False, "ambient_chatter": False}

        def fail(exc):
            captures["errors"].append(str(exc))
            traceback.print_exc()
            QApplication.instance().exit(1)

        def complete():
            try:
                if captures["results"] != [True, True]:
                    raise AssertionError(f"launch results: {captures['results']!r}")
                if len(captures["launches"]) != 2 or any(args for _, args, _ in captures["launches"]):
                    raise AssertionError("Both actions must launch with no text arguments")
                if captures["chat"].input.text() != sentinel:
                    raise AssertionError("Chat input was unexpectedly changed")
                if not toolkit_window_visible():
                    raise AssertionError("Toolkit native window was not visible")
                print("PASS: tray and chat each dispatched a detached launch with no arguments")
                print("PASS: chat input remained unchanged; a toolkit window was visible")
                print("LIMIT: an existing window may have been reused; its content was not inspected")
                QApplication.instance().quit()
            except Exception as exc:
                fail(exc)

        def click_chat():
            try:
                chat = captures["chat"]
                chat.show_near_pet()
                chat.input.setText(sentinel)
                chat.prompt_button.click()
                QTimer.singleShot(3000, complete)
            except Exception as exc:
                fail(exc)

        def click_tray():
            try:
                menu = captures["tray"].contextMenu()
                actions = [a for a in menu.actions() if a.text() == "프롬프트 정리 열기"]
                if len(actions) != 1:
                    raise AssertionError("Toolkit tray action missing or duplicated")
                actions[0].trigger()
                QTimer.singleShot(3000, click_chat)
            except Exception as exc:
                fail(exc)

        class CapturedApplication(QApplication):
            def __init__(self, args):
                super().__init__(args)
                QTimer.singleShot(1000, click_tray)
                QTimer.singleShot(20000, lambda: QApplication.instance().exit(2))

        pet_main.QApplication = CapturedApplication
        # Avoid passing the smoke script's path/argument to Qt.
        sys.argv = [sys.argv[0]]
        result = pet_main.main()
        if result == 0:
            if not toolkit_window_visible():
                print("FAIL: toolkit window not visible after the pet exited")
                return 1
            print("PASS: toolkit window remained visible after the pet exited")
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("toolkit_exe", type=Path)
    args = parser.parse_args()
    raise SystemExit(run(args.toolkit_exe))
