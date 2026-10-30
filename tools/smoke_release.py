"""Exercise a portable ZIP in an isolated temporary directory on Windows.

This creates only synthetic data, replaces main.py only in the temporary
extraction, runs that copy's native launcher, and closes its own test process.
It never launches or terminates an existing user instance.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

WRAPPER = '''from pathlib import Path
from PySide6.QtCore import QObject, QSettings, QTimer, Signal
from PySide6.QtWidgets import QApplication
import json
import _production_main as entry
from chat import ChatPanel

class IsolatedChatPanel(ChatPanel):
    def __init__(self, *args, **kwargs):
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

class TestApplication(QApplication):
    def __init__(self, args):
        super().__init__(args)
        QTimer.singleShot(1800, self.check)
        QTimer.singleShot(10000, lambda: self.exit(2))
    def check(self):
        from chat import ChatPanel
        panels = [item for item in self.topLevelWidgets() if isinstance(item, ChatPanel)]
        if len(panels) != 1:
            self.exit(3)
            return
        panel = panels[0]
        panel.show_prompt_near_pet()
        prompt = panel.prompt_conversation
        prompt.provider.setCurrentIndex(prompt.provider.findData("offline"))
        prompt.input.setPlainText("회의록을 세 항목으로 정리해 주세요.")
        prompt.submit()
        self.panel = panel
        QTimer.singleShot(300, self.done)
    def done(self):
        prompt = self.panel.prompt_conversation
        if prompt._busy:
            QTimer.singleShot(50, self.done)
            return
        if not prompt.results or "회의록을 세 항목으로" not in prompt.result.toPlainText():
            self.exit(4)
            return
        Path("qa-result.json").write_text(json.dumps({"offline_result": True}), encoding="utf-8")
        self.quit()

class NoNetworkUpdateChecker(QObject):
    completed = Signal(object)
    def check(self, manual=False):
        return None

entry.QApplication = TestApplication
entry.ChatPanel = IsolatedChatPanel
entry.UpdateChecker = NoNetworkUpdateChecker
raise SystemExit(entry.main())
'''


def run(archive_path: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Windows is required")
    with tempfile.TemporaryDirectory(prefix="syeon-release-smoke-") as tmp:
        root = Path(tmp).resolve()
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            if len(set(names)) != len(names) or sum(item.file_size for item in archive.infolist()) > 500_000_000:
                raise ValueError("Invalid or unexpectedly large archive")
            for name in names:
                parts = PurePosixPath(name).parts
                if not parts or name.startswith("/") or ".." in parts or "\\" in name or ":" in name:
                    raise ValueError("Unsafe archive path")
            archive.extractall(root)
        (root / "main.py").rename(root / "_production_main.py")
        (root / "main.py").write_text(WRAPPER, encoding="utf-8")
        env = os.environ.copy()
        env.pop("QT_QPA_PLATFORM", None)
        # Keep possible test-only application data separate from the user's.
        env["APPDATA"] = str(root / "qa-appdata")
        env["LOCALAPPDATA"] = str(root / "qa-localappdata")
        result = subprocess.run([str(root / "SyeonPet.exe")], cwd=root, env=env,
                                timeout=20, check=False,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        report = root / "qa-result.json"
        if result.returncode != 0 or not report.is_file():
            raise RuntimeError(f"Isolated GUI smoke failed: exit={result.returncode}, report={report.exists()}")
        if not json.loads(report.read_text(encoding="utf-8")).get("offline_result"):
            raise RuntimeError("No offline result")
        print("PASS: extracted native launcher opened Qt pet and produced synthetic offline result")
        print("LIMIT: synthetic UI, no external AI, no user files, no other Windows machine")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    run(parser.parse_args().archive.resolve(strict=True))
