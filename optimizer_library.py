"""User-controlled library, compatible with prompt-toolkit version 1 backups.

The desktop toolkit's WebView2 storage is never read or changed here.
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QLockFile, QStandardPaths, Signal
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel,
                               QListWidget, QMessageBox, QPushButton,
                               QTextEdit, QVBoxLayout)

LIMIT = 500
MAX_BYTES = 20 * 1024 * 1024
TASKS = {"auto", "general", "writing", "coding", "research", "learning", "analysis", "creative"}
DETAILS = {"balanced", "concise", "detailed"}


def _length(value: str) -> int:
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def validate_library(document: object) -> list[dict]:
    if (not isinstance(document, dict) or type(document.get("version")) is not int
            or document["version"] != 1 or not isinstance(document.get("items"), list)
            or len(document["items"]) > LIMIT):
        raise ValueError("지원하지 않는 백업 형식이거나 항목이 너무 많습니다.")
    result, seen = [], set()
    for item in document["items"]:
        if not isinstance(item, dict):
            raise ValueError("항목 형식이 올바르지 않습니다.")
        fields = ("id", "title", "prompt", "result", "task", "detail", "createdAt")
        if not all(isinstance(item.get(k), str) for k in fields):
            raise ValueError("항목 형식이 올바르지 않습니다.")
        if (not item["id"].strip() or _length(item["id"]) > 200 or item["id"] in seen
                or _length(item["title"]) > 120 or not item["prompt"].strip()
                or _length(item["prompt"]) > 100000 or _length(item["result"]) > 100000
                or item["task"] not in TASKS or item["detail"] not in DETAILS
                or type(item.get("archived", False)) is not bool
                or type(item.get("stale", False)) is not bool
                or not isinstance(item.get("mode", ""), str)
                or _length(item.get("mode", "")) > 80):
            raise ValueError("항목 형식이 올바르지 않습니다.")
        revisions = item.get("syeonRevisions", [])
        if (not isinstance(revisions, list) or len(revisions) > 500
                or any(not isinstance(value, str) or _length(value) > 2000 for value in revisions)
                or sum(_length(value) for value in revisions) > 100000):
            raise ValueError("대화의 추가 조건 형식이 올바르지 않습니다.")
        try:
            datetime.fromisoformat(item["createdAt"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("항목의 저장 날짜가 올바르지 않습니다.") from exc
        seen.add(item["id"])
        result.append({k: item[k] for k in fields} | {
            "archived": item.get("archived", False), "stale": item.get("stale", False),
            "mode": item.get("mode", ""), "syeonRevisions": list(revisions)})
    return result


def parse_backup(data: bytes) -> list[dict]:
    if len(data) > MAX_BYTES:
        raise ValueError("백업 파일은 20MB 이하여야 합니다.")
    try:
        return validate_library(json.loads(data.decode("utf-8-sig")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("JSON 파일을 읽지 못했습니다.") from exc


def merge_libraries(existing: list[dict], incoming: list[dict]) -> tuple[list[dict], int]:
    current = validate_library({"version": 1, "items": existing})
    additions = validate_library({"version": 1, "items": incoming})
    by_id = {item["id"]: item for item in current}
    reserved = set(by_id) | {item["id"] for item in additions}
    merged = current[:]
    added = 0
    for item in additions:
        item = item.copy()
        if item["id"] in by_id:
            if by_id[item["id"]] == item:
                continue
            for _ in range(20):
                new_id = str(uuid.uuid4())
                if new_id not in reserved:
                    break
            else:
                raise ValueError("새 항목 ID를 만들지 못했습니다.")
            item["id"] = new_id
            reserved.add(new_id)
        by_id[item["id"]] = item
        merged.append(item)
        added += 1
        if len(merged) > LIMIT:
            raise ValueError("보관함은 최대 500개까지 저장할 수 있습니다.")
    return merged, added


def _atomic_write(path: Path, payload: bytes) -> None:
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".library-", suffix=".tmp", delete=False) as f:
            name = f.name
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        name = None
    finally:
        if name:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass


class LibraryStore:
    def __init__(self, path: Path | None = None):
        if path is None:
            root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
            if not root:
                raise OSError("사용자 저장 폴더를 찾을 수 없습니다.")
            path = Path(root) / "SyeonPet" / "prompt-assistant" / "library.json"
        self.path = Path(path)

    def load(self) -> list[dict]:
        try:
            with self.path.open("rb") as f:
                return parse_backup(f.read(MAX_BYTES + 1))
        except FileNotFoundError:
            return []

    def merge(self, incoming: list[dict]) -> tuple[list[dict], int]:
        # Lock before reading as multiple pet processes may write concurrently.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self.path) + ".lock")
        if not lock.tryLock(0):
            raise OSError("다른 션 창에서 보관함을 사용 중입니다. 잠시 후 다시 시도하세요.")
        try:
            merged, count = merge_libraries(self.load(), incoming)
            if count:
                self._write(merged)
            return merged, count
        finally:
            lock.unlock()

    def _write(self, items: list[dict]) -> None:
        items = validate_library({"version": 1, "items": items})
        payload = json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=2).encode("utf-8")
        if len(payload) > MAX_BYTES:
            raise ValueError("보관함 크기가 20MB를 초과합니다.")
        _atomic_write(self.path, payload)


class LibraryDialog(QDialog):
    """Open with open(), retain a reference, and connect restored(dict)."""
    restored = Signal(dict)

    def __init__(self, parent, current_original, current_result, task="auto", detail="balanced", revisions=None):
        super().__init__(parent)
        self.setWindowTitle("션의 프롬프트 보관함")
        self.resize(560, 440)
        self.store = LibraryStore()
        self.current = (current_original, current_result, task, detail, list(revisions or []))
        self.items = []
        self._dialogs = []
        layout = QVBoxLayout(self)
        note = QLabel("직접 저장한 작업만 보관합니다. 기존 툴킷의 보관함은 별도입니다. "
                      "기존 툴킷으로 가져와 다시 백업하면 션의 추가 대화 조건은 빠질 수 있습니다.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.entries = QListWidget()
        self.entries.setAccessibleName("저장된 프롬프트 목록")
        self.entries.currentRowChanged.connect(self._preview)
        layout.addWidget(self.entries, 1)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("선택한 프롬프트 미리보기")
        layout.addWidget(self.preview, 2)
        row = QHBoxLayout()
        for label, callback in (("현재 작업 저장", self._save), ("선택 항목 불러오기", self._restore),
                                ("JSON 백업", self._export), ("JSON 가져오기", self._import)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self._refresh()

    def _notify(self, title, message, icon=QMessageBox.Icon.Information):
        box = QMessageBox(icon, title, message, QMessageBox.StandardButton.Ok, self)
        self._dialogs.append(box)
        box.finished.connect(lambda: self._dialogs.remove(box))
        box.open()

    def _refresh(self):
        try:
            self.items = self.store.load()
        except (OSError, ValueError) as exc:
            self._notify("보관함을 읽지 못했습니다", f"기존 파일은 변경하지 않았습니다.\n{exc}", QMessageBox.Icon.Warning)
            return
        self.entries.clear()
        for item in self.items:
            self.entries.addItem(item["title"] or item["prompt"][:40])
        self.preview.clear()

    def _preview(self, row):
        if 0 <= row < len(self.items):
            item = self.items[row]
            revisions = "\n".join(item.get("syeonRevisions", [])) or "없음"
            self.preview.setPlainText(f"원래 요청:\n{item['prompt']}\n\n추가 조건:\n{revisions}\n\n정리 결과:\n{item['result']}")
        else:
            self.preview.clear()

    def _save(self):
        prompt, result, task, detail, revisions = self.current
        if not prompt.strip() or not result.strip():
            self._notify("저장할 내용이 없습니다", "원래 요청과 정리 결과를 먼저 작성해 주세요.")
            return
        # Match the existing toolkit's UTF-16 title limit, including emoji.
        title = ""
        for ch in prompt.strip():
            if _length(title + ch) > 120:
                break
            title += ch
        item = {"id": str(uuid.uuid4()), "title": title, "prompt": prompt,
                "result": result, "task": task, "detail": detail,
                "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "mode": "", "archived": False, "stale": False, "syeonRevisions": revisions}
        try:
            self.store.merge([item])
            self._refresh()
            self._notify("저장 완료", "현재 작업을 션의 보관함에 저장했습니다.")
        except (OSError, ValueError) as exc:
            self._notify("저장 실패", f"기존 보관함은 변경하지 않았습니다.\n{exc}", QMessageBox.Icon.Warning)

    def _restore(self):
        row = self.entries.currentRow()
        if not 0 <= row < len(self.items):
            self._notify("항목 선택", "불러올 항목을 먼저 선택해 주세요.")
            return
        item = self.items[row].copy()
        box = QMessageBox(QMessageBox.Icon.Question, "선택 항목 불러오기",
                          "현재 대화의 원래 요청과 결과를 선택한 항목으로 바꿀까요? 작성 중인 미전송 입력도 확인해 주세요.",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        self._dialogs.append(box)
        def done(result):
            self._dialogs.remove(box)
            if result == QMessageBox.StandardButton.Yes:
                self.restored.emit(item)
                self.accept()
        box.finished.connect(done)
        box.open()

    def _file_dialog(self, title, save, handler):
        dialog = QFileDialog(self, title)
        dialog.setNameFilter("JSON 파일 (*.json)")
        dialog.setDefaultSuffix("json")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave if save else QFileDialog.AcceptMode.AcceptOpen)
        self._dialogs.append(dialog)
        dialog.fileSelected.connect(handler)
        dialog.finished.connect(lambda: self._dialogs.remove(dialog))
        dialog.open()

    def _export(self):
        def write(path):
            try:
                items = self.store.load()
                data = json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=2).encode("utf-8")
                # User selected the destination in the save dialog. Keep the source untouched.
                _atomic_write(Path(path), data)
                self._notify("백업 완료", "JSON 백업을 저장했습니다.")
            except (OSError, ValueError) as exc:
                self._notify("백업 실패", str(exc), QMessageBox.Icon.Warning)
        self._file_dialog("JSON 백업 저장", True, write)

    def _import(self):
        def read(path):
            try:
                with Path(path).open("rb") as f:
                    incoming = parse_backup(f.read(MAX_BYTES + 1))
                _, count = self.store.merge(incoming)
                self._refresh()
                self._notify("가져오기 완료", f"{count}개 항목을 보관함에 추가했습니다.")
            except (OSError, ValueError) as exc:
                self._notify("가져오기 실패", f"기존 보관함은 변경하지 않았습니다.\n{exc}", QMessageBox.Icon.Warning)
        self._file_dialog("JSON 백업 가져오기", False, read)
