"""Launch the existing prompt toolkit without changing its data or lifetime."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QSettings
from PySide6.QtWidgets import QFileDialog, QMessageBox


class PromptToolkitLauncher:
    """Remember the executable independently of the pet's config.json."""

    def __init__(self):
        self._settings = QSettings("SyeonPet", "PromptToolkit")

    @staticmethod
    def _application_dir() -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent

    @staticmethod
    def _valid_executable(path: Path) -> bool:
        return path.suffix.lower() == ".exe" and path.is_file()

    def _configured_path(self) -> Path | None:
        value = self._settings.value("executablePath", "", type=str).strip()
        if value:
            return Path(value)
        root = self._application_dir()
        # Optional side-by-side distribution. Never guess a user's personal path.
        for candidate in (root / "prompt-toolkit-desktop.exe",
                          root / "prompt-toolkit" / "prompt-toolkit-desktop.exe"):
            if self._valid_executable(candidate):
                return candidate
        return None

    def configure(self, parent=None) -> bool:
        current = self._configured_path()
        start = str(current.parent) if current else str(self._application_dir())
        selected, _ = QFileDialog.getOpenFileName(
            parent, "프롬프트 툴킷 실행 파일 선택", start,
            "실행 파일 (*.exe)")
        if not selected:
            return False
        path = Path(selected)
        if not self._valid_executable(path):
            QMessageBox.warning(parent, "실행 파일 확인",
                                "존재하는 .exe 파일을 선택해 주세요.")
            return False
        self._settings.setValue("executablePath", str(path.resolve()))
        self._settings.sync()
        if self._settings.status() != QSettings.Status.NoError:
            QMessageBox.warning(parent, "설정 저장 실패",
                                "실행 파일 경로를 저장하지 못했습니다. "
                                "사용자 설정 쓰기 권한을 확인해 주세요.")
            return False
        return True

    def open(self, parent=None) -> bool:
        path = self._configured_path()
        if not path or not self._valid_executable(path):
            if path:
                QMessageBox.warning(parent, "프롬프트 툴킷을 찾을 수 없음",
                                    "저장된 실행 파일이 없거나 올바르지 않습니다. "
                                    "현재 사용할 실행 파일을 다시 선택해 주세요.")
            if not self.configure(parent):
                return False
            path = self._configured_path()
        if not path or not self._valid_executable(path):
            return False
        # Launch every time: Tauri's single-instance handler restores a hidden
        # window. The detached process remains independent when the pet exits.
        started, _pid = QProcess.startDetached(str(path), [], str(path.parent))
        if not started:
            QMessageBox.warning(parent, "프롬프트 툴킷 실행 실패",
                                "실행 파일을 열지 못했습니다. 파일과 실행 권한을 "
                                "확인해 주세요. 실행 후 창이 나타나지 않으면 "
                                "Microsoft Edge WebView2 Runtime도 확인해 주세요.")
        return bool(started)
