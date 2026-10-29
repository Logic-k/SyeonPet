"""Conversational prompt drafting, hosted inside the pet's existing window."""
from __future__ import annotations

import re
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
                               QWidget)

from optimizer_core import TASKS, create_offline_prompt, review_result, validate_input
from optimizer_ai import optimize_ai


class _ReplySignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class _StatusText(QTextEdit):
    """A compact, scrollable status area for long warnings or errors."""
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAccessibleName("정리 상태와 확인 사항")
        self.setMinimumHeight(44)
        self.setMaximumHeight(60)
        self.setPlainText(text)

    def setText(self, text):
        self.setPlainText(text)

    def text(self):
        return self.toPlainText()


def _append_conditions(previous, additions):
    sections = []
    for item in additions:
        longest = max([2] + [len(match.group()) for match in re.finditer(r"`+", item)])
        fence = "`" * (longest + 1)
        sections.append("사용자가 추가한 조건:\n" + fence + "\n" + item + "\n" + fence)
    return previous + ("\n\n" + "\n\n".join(sections) if sections else "")


class PromptConversation(QWidget):
    """Keep every source and result available while a user refines a request."""

    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.original = ""
        self.revisions: list[str] = []
        self.results: list[str] = []
        self._result_revision_counts: list[int] = []
        self._shown_result = -1
        self._busy = False
        self._sleep_paused = False
        self._signals = _ReplySignals(self)
        self._signals.completed.connect(self._finish)
        self._signals.failed.connect(self._fail)

        self.conversation = QTextEdit()
        self.conversation.setReadOnly(True)
        self.conversation.setAccessibleName("션과 나눈 프롬프트 대화")
        self.conversation.setPlaceholderText("요청을 적어 주시면 션이 정리를 도와드려요.")
        self.conversation.setPlainText("션: 어떤 요청을 정리할까요?\n처음에는 원하는 일을 적고, 이후에는 추가 조건을 말씀해 주세요.")

        self.provider = QComboBox()
        self.provider.setAccessibleName("정리 방식")
        self.provider.addItem("기본 정리 (AI 사용 안 함)", "offline")
        self.provider.addItem("AI 정리 · Vireonix", "vireonix")
        self.provider.addItem("AI 정리 · Pollinations Classic", "pollinations-classic")
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.provider_note = QLabel()
        self.provider_note.setWordWrap(True)
        self._provider_changed()
        self.task = QComboBox()
        self.task.setAccessibleName("작업 유형")
        for key, label in TASKS.items():
            self.task.addItem(label or "자동", key)
        self.detail = QComboBox()
        self.detail.setAccessibleName("상세 수준")
        for key, label in (("concise", "간결하게"), ("balanced", "적절하게"), ("detailed", "상세하게")):
            self.detail.addItem(label, key)
        self.detail.setCurrentIndex(1)

        self.input = QTextEdit()
        self.input.setAccessibleName("요청 또는 추가 조건")
        self.input.setPlaceholderText("요청이나 추가 조건을 적어 주세요")
        self.input.setMinimumHeight(65)
        self.input.setMaximumHeight(110)
        self.send = QPushButton("션에게 보내기")
        self.send.clicked.connect(self.submit)
        self.template = QPushButton("업무·일반 템플릿")
        self.template.setToolTip("교육지원청 업무와 일반 용도의 예시를 검색해 입력란에 넣습니다.")
        self.template.clicked.connect(self._insert_template)
        self.reset = QPushButton("새 요청")
        self.reset.clicked.connect(self._new_request)
        self.library = QPushButton("보관함")
        self.library.clicked.connect(self._open_library)

        self.result_choice = QComboBox()
        self.result_choice.setAccessibleName("이전 정리 결과 선택")
        self.result_choice.currentIndexChanged.connect(self._select_result)
        self.result = QTextEdit()
        self.result.setAccessibleName("정리한 프롬프트 편집")
        self.result.setPlaceholderText("정리한 결과가 여기에 표시됩니다. 직접 수정할 수 있어요.")
        self.copy = QPushButton("결과 복사")
        self.copy.clicked.connect(self._copy_result)
        self.status = _StatusText("기본 정리는 입력을 구조화하며 AI 재작성은 하지 않습니다.")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        layout.addWidget(QLabel("션과 대화"))
        layout.addWidget(self.conversation, 2)
        row = QHBoxLayout()
        row.addWidget(QLabel("정리 방식"))
        row.addWidget(self.provider, 1)
        layout.addLayout(row)
        layout.addWidget(self.provider_note)
        row = QHBoxLayout()
        row.addWidget(QLabel("작업"))
        row.addWidget(self.task, 1)
        row.addWidget(QLabel("상세"))
        row.addWidget(self.detail, 1)
        layout.addLayout(row)
        layout.addWidget(self.input)
        row = QHBoxLayout()
        row.addWidget(self.template)
        row.addWidget(self.library)
        row.addWidget(self.reset)
        row.addStretch()
        row.addWidget(self.send)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("정리 결과"))
        row.addWidget(self.result_choice, 1)
        row.addWidget(self.copy)
        layout.addLayout(row)
        layout.addWidget(self.result, 2)
        layout.addWidget(self.status)

    def _provider_changed(self):
        if self.provider.currentData() == "offline":
            self.provider_note.setText("입력한 문장을 그대로 구조화합니다. 자연스러운 재작성은 AI 방식을 선택해 주세요.")
        else:
            self.provider_note.setText("선택한 외부 서비스로 원문과 추가 조건, 이전 결과가 전송됩니다. 전송 전 다시 확인합니다.")

    def _insert_template(self):
        from optimizer_templates import show_template_picker
        existing = getattr(self, "_template_dialog", None)
        if existing is not None:
            try:
                if existing.isVisible():
                    existing.raise_()
                    existing.activateWindow()
                    return
            except RuntimeError:
                # WA_DeleteOnClose may have destroyed the underlying widget.
                pass
        def insert(chosen):
            cursor = self.input.textCursor()
            cursor.insertText(chosen)
            self.input.setTextCursor(cursor)
            self.input.setFocus()
        self._template_dialog = show_template_picker(self, insert)

    def _open_library(self):
        from optimizer_library import LibraryDialog
        self._save_result()
        self._library_snapshot = self._draft_snapshot()
        selected_count = (self._result_revision_counts[self._shown_result]
                          if 0 <= self._shown_result < len(self._result_revision_counts) else 0)
        missing = self.revisions[selected_count:]
        selected_result = self.result.toPlainText()
        # An earlier displayed result may predate later turns. Preserve all
        # committed conditions in the saved copy without changing the editor.
        saved_result = _append_conditions(selected_result, missing) if selected_result else selected_result
        if missing:
            self.status.setText("이전 회차를 보관할 때 이후 추가 조건을 저장본에 함께 붙입니다. 화면의 결과는 그대로입니다.")
        self._library_dialog = LibraryDialog(self, self.original, saved_result,
                                             self.task.currentData(), self.detail.currentData(),
                                             revisions=list(self.revisions))
        self._library_dialog.restored.connect(self._restore_library)
        self._library_dialog.open()

    def _restore_library(self, item):
        # The picker is modeless and may change the editor while the library
        # dialog is open. Do not discard those later edits without a new,
        # explicit confirmation of the changed state.
        if self._busy:
            self.status.setText("정리 중에는 보관함을 불러올 수 없어요. 기존 입력은 그대로 남아 있습니다.")
            return
        current = self._draft_snapshot()
        if current != getattr(self, "_library_snapshot", current):
            self._confirm("변경된 입력 확인", "보관함을 연 뒤 대화나 입력이 바뀌었어요. 현재 내용을 바꾸고 불러올까요?",
                          lambda: self._apply_library_if_unchanged(item, current))
            return
        self._apply_library(item)

    def _apply_library_if_unchanged(self, item, expected):
        if self._busy or self._draft_snapshot() != expected:
            self.status.setText("입력이 다시 바뀌어 불러오지 않았어요. 기존 내용은 그대로 남아 있습니다.")
            return
        self._apply_library(item)

    def _apply_library(self, item):
        self.original = item.get("prompt", "")
        self.task.setCurrentIndex(max(0, self.task.findData(item.get("task", "auto"))))
        self.detail.setCurrentIndex(max(0, self.detail.findData(item.get("detail", "balanced"))))
        self.revisions = list(item.get("syeonRevisions", []))
        self.results = [item.get("result", "")]
        self._result_revision_counts = [len(self.revisions)]
        self._shown_result = -1
        self.result_choice.clear()
        self.result_choice.addItem("보관함에서 복원한 결과")
        self._select_result(0)
        self.conversation.setPlainText("션: 보관한 요청과 결과를 불러왔어요. 추가 조건을 말씀해 주세요.\n\n나: " + self.original +
                                       "".join("\n\n나: " + revision for revision in self.revisions))
        self.input.clear()
        self.status.setText("복원한 원문과 결과를 확인해 주세요.")

    def _save_result(self):
        if 0 <= self._shown_result < len(self.results):
            self.results[self._shown_result] = self.result.toPlainText()

    def _select_result(self, index):
        self._save_result()
        self._shown_result = index
        self.result.setPlainText(self.results[index] if 0 <= index < len(self.results) else "")

    def _copy_result(self):
        value = self.result.toPlainText()
        if value:
            QApplication.clipboard().setText(value)
            self.status.setText("결과를 복사했어요. 원문과 조건을 확인한 뒤 사용해 주세요.")

    def _new_request(self):
        if self._busy:
            return
        if self.original or self.input.toPlainText() or self.result.toPlainText():
            expected = self._draft_snapshot()
            self._confirm("새 요청", "현재 대화와 결과를 지우고 새 요청을 시작할까요?\n필요한 내용은 먼저 복사해 주세요.",
                          lambda: self._clear_if_unchanged(expected))
            return
        self._clear_request()

    def _draft_snapshot(self):
        return (self.original, tuple(self.revisions), tuple(self.results), self._shown_result,
                self.input.toPlainText(), self.result.toPlainText())

    def _clear_if_unchanged(self, expected):
        if self._busy or self._draft_snapshot() != expected:
            self.status.setText("확인 중 입력이 바뀌어 새 요청을 시작하지 않았어요. 기존 내용은 그대로 남아 있습니다.")
            return
        self._clear_request()

    def _clear_request(self):
        self.original = ""
        self.revisions.clear()
        self.results.clear()
        self._result_revision_counts.clear()
        self._shown_result = -1
        self.result_choice.clear()
        self.result.clear()
        self.input.clear()
        self.conversation.setPlainText("션: 어떤 요청을 정리할까요?")
        self.status.setText("새 요청을 입력해 주세요.")

    def _set_busy(self, busy):
        busy = bool(busy)
        changed = busy != self._busy
        self._busy = busy
        for control in (self.input, self.send, self.provider, self.task, self.detail, self.template, self.library,
                        self.reset, self.result, self.result_choice):
            control.setEnabled(not busy)
        self.send.setEnabled(not busy and not self._sleep_paused)
        if changed:
            self.busy_changed.emit(busy)

    @property
    def is_busy(self) -> bool:
        return self._busy

    def has_pending_work(self) -> bool:
        """Whether closing the application could discard conversation content."""
        return bool(self.original or self.revisions or self.results
                    or self.input.toPlainText() or self.result.toPlainText()
                    or self._open_template_snapshot() is not None)

    def _open_template_snapshot(self):
        picker = getattr(self, "_template_dialog", None)
        if picker is None:
            return None
        try:
            if not picker.isVisible():
                return None
            # Include edits on non-selected templates as well as the current
            # preview: the picker can retain multiple modified drafts.
            return (picker._current_id, picker.preview.toPlainText(),
                    tuple(sorted(picker._drafts.items())), picker.search.text())
        except RuntimeError:
            return None  # Qt deleted the picker after it was closed.

    def quit_snapshot(self):
        return self._request_snapshot(), self._open_template_snapshot()

    def set_sleep_paused(self, paused: bool):
        """Pause new requests without cancelling a request already in flight."""
        self._sleep_paused = bool(paused)
        self.send.setEnabled(not self._busy and not self._sleep_paused)

    def _confirm(self, title, text, callback):
        dialog = QMessageBox(QMessageBox.Icon.Question, title, text,
                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        self._confirmation = dialog
        dialog.finished.connect(lambda value: callback() if value == QMessageBox.StandardButton.Yes else None)
        dialog.open()

    def submit(self):
        if self._busy or self._sleep_paused:
            return
        text = self.input.toPlainText()
        if not text.strip():
            self.status.setText("먼저 요청 또는 추가 조건을 입력해 주세요.")
            return
        provider = self.provider.currentData()
        task, detail = self.task.currentData(), self.detail.currentData()
        original = self.original or text
        previous = self.result.toPlainText() if self.original else ""
        old_context = "\n\n".join(self.revisions)
        revision = text if self.original else ""
        next_context = "\n\n".join([*self.revisions, revision] if revision else self.revisions)
        count = (self._result_revision_counts[self._shown_result]
                 if 0 <= self._shown_result < len(self._result_revision_counts) else 0)
        pending_revisions = [*self.revisions[count:], revision] if revision else []
        if provider != "offline":
            service = "Vireonix" if provider == "vireonix" else "Pollinations Classic"
            info = (f"원래 요청, 추가 조건과 이전 결과를 {service}에 전송합니다. "
                    "제공자는 내용을 저장하거나 서비스 개선에 사용할 수 있습니다. "
                    "학생·학부모·직원 식별정보, 민원 원문과 비공개 문서는 제외하거나 "
                    "가상 내용으로 바꾸고 기관의 사용 정책과 허용 서비스를 확인해 주세요.\n\n전송하시겠어요?")
            expected = self._request_snapshot()
            self._confirm("외부 AI 전송 확인", info,
                          lambda: self._submit_if_unchanged(expected, text, original, revision, provider, old_context, next_context, previous, task, detail, pending_revisions))
            return
        self._submit_confirmed(text, original, revision, provider, old_context, next_context, previous, task, detail, pending_revisions)

    def _request_snapshot(self):
        return (self._draft_snapshot(), self.provider.currentData(), self.task.currentData(),
                self.detail.currentData())

    def _submit_if_unchanged(self, expected, *args):
        if self._request_snapshot() != expected:
            self.status.setText("확인 중 입력이나 설정이 바뀌어 전송하지 않았어요. 내용을 다시 확인해 주세요.")
            return
        self._submit_confirmed(*args)

    def _submit_confirmed(self, text, original, revision, provider, old_context, next_context, previous, task, detail, pending_revisions):
        # The pet may have been put to sleep while the confirmation was open.
        if self._sleep_paused:
            self.status.setText("션이 완전히 자고 있어요. 깨운 후 다시 보내 주세요. 입력은 그대로 남아 있습니다.")
            return
        # A second confirmation can return after another request has started.
        if self._busy:
            self.status.setText("이미 정리 중이에요. 입력은 그대로 남아 있습니다.")
            return
        self._pending = (text, original, revision, provider)
        self._set_busy(True)
        self.status.setText("션이 정리하고 있어요…" if provider != "offline" else "기본 정리 중…")

        def work():
            try:
                if provider == "offline":
                    if revision:
                        validate_input(original, next_context, task, detail)
                        if not previous.strip():
                            raise ValueError("이전 정리 결과가 비어 있어 추가 조건을 붙일 수 없습니다. 이전 결과를 선택하거나 새 요청을 시작해 주세요.")
                        reply = {"result": _append_conditions(previous, pending_revisions),
                                 "notes": ["수정한 이전 결과를 유지하고 추가 조건을 그대로 붙였습니다. AI 재작성은 하지 않았습니다."],
                                 "mode": "offline"}
                    else:
                        reply = create_offline_prompt(original, context=next_context, task=task, detail=detail)
                else:
                    reply = optimize_ai(original, context=old_context, provider=provider,
                                        revision=revision, previous_result=previous, task=task, detail=detail)
                try:
                    self._signals.completed.emit(reply)
                except RuntimeError:  # application closed while request was in flight
                    pass
            except Exception as exc:
                try:
                    self._signals.failed.emit(str(exc))
                except RuntimeError:
                    pass

        # A daemon avoids blocking app shutdown if an external provider stalls.
        # The Qt-owned signal object only schedules UI work on the GUI thread.
        try:
            threading.Thread(target=work, daemon=True, name="prompt-optimizer").start()
        except RuntimeError:
            self._fail("정리 작업을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.")

    def _finish(self, reply):
        text, original, revision, provider = self._pending
        self._save_result()
        self.original = original
        if revision:
            self.revisions.append(revision)
        cursor = self.conversation.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText("\n\n나: " + text + "\n션: " + ("AI로 정리했어요. 아래에서 살펴보고 수정해 주세요." if provider != "offline" else "입력과 조건을 기본 구조로 정리했어요."))
        self.conversation.setTextCursor(cursor)
        # A modeless template picker can still insert text while the input is
        # disabled. Only clear the exact draft that was submitted; if it has
        # changed in the meantime, preserve it for the next request.
        input_changed = self.input.toPlainText() != text
        if not input_changed:
            self.input.clear()
        self.results.append(reply["result"])
        self._result_revision_counts.append(len(self.revisions))
        self.result_choice.addItem(f"{len(self.results)}번째 결과")
        self.result_choice.setCurrentIndex(len(self.results) - 1)
        # A first item can already be selected by addItem before setCurrentIndex.
        if self._shown_result != len(self.results) - 1:
            self._select_result(len(self.results) - 1)
        notes = list(reply.get("notes", []))
        if provider != "offline":
            notes.extend(review_result(self.original, "\n\n".join(self.revisions), reply["result"]))
        if input_changed:
            notes.append("정리 중 바뀐 입력은 그대로 남겨 두었어요. 다음 요청에 사용할 내용을 확인해 주세요.")
        self.status.setText("\n".join(notes) if notes else "원래 의도와 조건을 확인해 주세요.")
        self._set_busy(False)
        self.input.setFocus()

    def _fail(self, message):
        self._set_busy(False)
        status = "정리하지 못했어요: " + message + "\n입력과 이전 결과는 그대로 남아 있습니다."
        provider = self._pending[3]
        if provider != "offline":
            status += ("\n" + ("션을 깨운 뒤 " if self._sleep_paused else "") +
                       "다시 시도하려면 ‘션에게 보내기’를 누르세요. "
                       "다른 AI 서비스나 기본 정리를 직접 선택할 수도 있어요. "
                       "AI로 보낼 때마다 전송 여부를 확인하며, 기본 정리는 AI 재작성이 아닙니다.")
        self.status.setText(status)
