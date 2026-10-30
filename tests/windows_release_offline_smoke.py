"""Run the published launcher with synthetic DNS/TCP failures in an isolated copy.

The computer stays online: only this test process has its network boundary
replaced. The real release UI, optimizer and Qt event loop are used.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

WRAPPER = r'''from pathlib import Path
import inspect, json, socket, time, traceback
from PySide6.QtCore import QObject, QSettings, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox
import _production_main as entry
from chat import ChatPanel
from pet import PetWidget
import optimizer_ai

state = {"phase": "offline", "dns": 0, "connect": 0}

class IsolatedChatPanel(ChatPanel):
    def __init__(self, *args, **kwargs):
        # QSettings(organization, application) may use the Windows registry
        # even after changing the default format. Explicit file-backed settings
        # with fallbacks disabled never read the user's conversation settings.
        settings_path = Path.cwd() / "qa-settings" / "conversation.ini"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        settings.setValue("opacityPercent", 91)
        settings.sync()
        kwargs["settings"] = settings
        super().__init__(*args, **kwargs)
        assert Path(self._settings.fileName()).resolve() == settings_path.resolve()
        assert self.opacity_slider.value() == 91

class IsolatedPet(PetWidget):
    def __init__(self, *args, **kwargs):
        if "wardrobe_settings" in inspect.signature(PetWidget.__init__).parameters:
            settings = QSettings(str(Path.cwd() / "qa-settings" / "wardrobe.ini"), QSettings.Format.IniFormat)
            settings.setFallbacksEnabled(False)
            kwargs["wardrobe_settings"] = settings
        super().__init__(*args, **kwargs)

def fail(exc):
    Path("qa-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    QApplication.instance().exit(1)

def blocked_dns(*args, **kwargs):
    state["dns"] += 1
    raise socket.gaierror(socket.EAI_NONAME, "synthetic DNS outage")

def blocked_tcp(*args, **kwargs):
    state["connect"] += 1
    raise OSError("synthetic network unavailable")

def check():
    try:
        p = state["panel"].prompt_conversation
        if p.is_busy:
            QTimer.singleShot(30, check)
            return
        if state["phase"] == "offline":
            assert p.original == "회의 결과를 세 항목으로 요약해 주세요."
            assert "회의 결과를 세 항목으로" in p.result.toPlainText()
            p.result.setPlainText("사용자가 수정한 이전 결과")
            p.input.setPlainText("새로운 추가 조건")
            p.provider.setCurrentIndex(p.provider.findData("vireonix"))
            p.send.click()
            assert p._confirmation.isVisible() and state["dns"] == 0
            state["phase"] = "dns"
            p._confirmation.done(int(QMessageBox.StandardButton.Yes))
            QTimer.singleShot(30, check)
        elif state["phase"] == "dns":
            assert state["dns"] == 1 and state["connect"] == 0
            assert "인터넷 연결과 DNS 설정" in p.status.text()
            assert p.original == "회의 결과를 세 항목으로 요약해 주세요."
            assert p.input.toPlainText() == "새로운 추가 조건"
            assert p.result.toPlainText() == "사용자가 수정한 이전 결과"
            assert p.send.isEnabled()
            optimizer_ai._resolve_public = lambda host: "8.8.8.8"
            p.send.click()
            assert p._confirmation.isVisible() and state["connect"] == 0
            state["phase"] = "tcp"
            p._confirmation.done(int(QMessageBox.StandardButton.Yes))
            QTimer.singleShot(30, check)
        else:
            assert state["connect"] == 1
            assert "인터넷 연결을 확인" in p.status.text()
            assert p.input.toPlainText() == "새로운 추가 조건"
            assert p.result.toPlainText() == "사용자가 수정한 이전 결과"
            assert p.send.isEnabled()
            Path("qa-result.json").write_text(json.dumps({"offline": True, "dns_failure": True, "tcp_failure": True, "input_preserved": True, "result_preserved": True}), encoding="utf-8")
            QApplication.instance().quit()
    except Exception as exc:
        fail(exc)

class TestApplication(QApplication):
    def __init__(self, args):
        super().__init__(args)
        QTimer.singleShot(800, self.start)
        QTimer.singleShot(15000, lambda: self.exit(2))
    def start(self):
        try:
            panels = [w for w in self.topLevelWidgets() if isinstance(w, ChatPanel)]
            assert len(panels) == 1
            state["panel"] = panels[0]
            panels[0].show_prompt_near_pet()
            p = panels[0].prompt_conversation
            assert p.provider.currentData() == "vireonix"
            p.provider.setCurrentIndex(p.provider.findData("offline"))
            p.input.setPlainText("회의 결과를 세 항목으로 요약해 주세요.")
            p.send.click()
            QTimer.singleShot(30, check)
        except Exception as exc:
            fail(exc)

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

entry.QApplication = TestApplication
entry.ChatPanel = IsolatedChatPanel
entry.PetWidget = IsolatedPet
if hasattr(entry, "UpdatePreparer"):
    entry.UpdatePreparer = NoNetworkUpdates
    import wardrobe, pet
    wardrobe.pack_root = lambda: Path.cwd() / "qa-packs"
    pet.pack_root = wardrobe.pack_root
else:
    entry.UpdateChecker = NoNetworkUpdates
optimizer_ai.socket.getaddrinfo = blocked_dns
optimizer_ai.socket.create_connection = blocked_tcp
raise SystemExit(entry.main())
'''


def run(archive: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Windows is required")
    with tempfile.TemporaryDirectory(prefix="syeon-release-network-") as tmp:
        root = Path(tmp).resolve()
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                parts = PurePosixPath(name).parts
                if not parts or name.startswith("/") or ".." in parts or "\\" in name or ":" in name:
                    raise ValueError("Unsafe archive path")
            z.extractall(root)
        (root / "main.py").rename(root / "_production_main.py")
        (root / "main.py").write_text(WRAPPER, encoding="utf-8")
        env = os.environ.copy()
        env.pop("QT_QPA_PLATFORM", None)
        env["APPDATA"] = str(root / "qa-appdata")
        env["LOCALAPPDATA"] = str(root / "qa-localappdata")
        result = subprocess.run([str(root / "SyeonPet.exe")], cwd=root, env=env,
                                timeout=20, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        error = root / "qa-error.txt"
        if result.returncode or not (root / "qa-result.json").is_file():
            raise RuntimeError(f"GUI failed: exit={result.returncode}; " + (error.read_text(encoding="utf-8") if error.exists() else "no report"))
        print((root / "qa-result.json").read_text(encoding="utf-8"))
        print("PASS: release launcher, actual Qt conversation and isolated simulated DNS/TCP outages")
        print("LIMIT: computer internet was not disconnected; no real AI service called")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    run(parser.parse_args().archive.resolve(strict=True))
