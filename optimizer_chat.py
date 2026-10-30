"""Conversational prompt drafting, hosted inside the pet's existing window."""
from __future__ import annotations

import re
import threading

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QApplication, QComboBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QTextEdit, QToolButton,
                               QVBoxLayout, QWidget)

from optimizer_core import TASKS, create_offline_prompt, review_result, validate_input
from optimizer_ai import optimize_ai

_FIRST_REQUEST_EXAMPLE = "예: 연수 안내문 작성, 날짜는 [확인 필요]"
_FOLLOWUP_EXAMPLE = "예: 위 내용을 세 항목으로 나눠 주세요."
_PLACEHOLDER = re.compile(r"\[([^\[\]\n]{2,40})\]")
_MAX_FILL_FIELDS = 8
# After a failed AI request, offer the other fixed service. Sending still goes
# through the normal per-request transmission confirmation.
_OTHER_AI = {"vireonix": ("pollinations-classic", "Pollinations Classic으로 보내기"),
             "pollinations-classic": ("vireonix", "Vireonix로 보내기")}


def _placeholders(text):
    """Distinct [placeholder] names in order of appearance."""
    names = []
    for match in _PLACEHOLDER.finditer(text):
        name = match.group(1).strip()
        if len(name) >= 2 and name not in names:
            names.append(name)
    return names


def _is_paste_slot(name):
    # Slots such as [회의록 본문 붙여 넣기] expect long, multi-line material.
    return "붙여" in name


class _ReplySignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class _StatusText(QTextEdit):
    """A compact, scrollable status area for long warnings or errors."""
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAccessibleName("정리 상태와 확인 사항")
        self.setToolTip("긴 안내는 이 영역에서 스크롤해 읽을 수 있습니다.")
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
    notice_raised = Signal()
    result_ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("promptConversation")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Explicit foregrounds and surfaces keep editors readable with either
        # the Windows light or dark system palette. The window opacity remains
        # controlled by ChatPanel and does not change the stored document text.
        self.setStyleSheet("""
            QWidget#promptConversation { background: #f3f6fa; }
            QWidget#promptConversation > QLabel { color: #172b3a; }
            QWidget#promptConversation > QLabel#sectionTitle { color: #172b3a; font-weight: 600; }
            QWidget#promptConversation > QLabel#providerNote { color: #344e60; }
            QWidget#promptConversation > QLabel#inlineNotice { background: #fff1ed;
                color: #722c1d; border: 1px solid #c38170; border-radius: 4px; padding: 4px 6px; }
            QWidget#promptConversation > QTextEdit, QWidget#promptConversation > QComboBox {
                background: #ffffff; color: #172b3a;
                border: 1px solid #9aaebc; border-radius: 4px; padding: 4px;
                selection-background-color: #195b83; selection-color: #ffffff;
            }
            QWidget#promptConversation > QTextEdit:focus, QWidget#promptConversation > QComboBox:focus { border: 2px solid #195b83; padding: 3px; }
            QWidget#promptConversation > QTextEdit#requestInput { placeholder-text-color: #586d7a; }
            QWidget#promptConversation > QTextEdit:disabled, QWidget#promptConversation > QComboBox:disabled { background: #e9eef2; color: #425a6a; }
            QWidget#promptConversation > QTextEdit#statusText { background: #eaf1f6; color: #172b3a; }
            QWidget#promptConversation > QTextEdit QScrollBar:vertical { background: #e5edf2; width: 11px; margin: 0; }
            QWidget#promptConversation > QTextEdit QScrollBar::handle:vertical { background: #6a8394; min-height: 20px; border-radius: 5px; }
            QWidget#promptConversation > QTextEdit QScrollBar::add-line:vertical, QWidget#promptConversation > QTextEdit QScrollBar::sub-line:vertical { height: 0; }
            QWidget#promptConversation > QComboBox QAbstractItemView { background: #ffffff; color: #172b3a;
                selection-background-color: #195b83; selection-color: #ffffff; }
            QWidget#promptConversation > QPushButton { background: #ffffff; color: #172b3a;
                border: 1px solid #8fa5b4; border-radius: 4px; padding: 4px 7px; }
            QWidget#promptConversation > QPushButton:hover { background: #e6f0f6; }
            QWidget#promptConversation > QPushButton:focus { border: 2px solid #195b83; padding: 3px 6px; }
            QWidget#promptConversation > QPushButton:disabled { background: #e9eef2; color: #526a79; border-color: #afbec8; }
            QWidget#promptConversation > QPushButton#primaryAction { background: #195b83; color: #ffffff;
                border: 1px solid #195b83; font-weight: 600; }
            QWidget#promptConversation > QPushButton#primaryAction:hover { background: #124967; }
            QWidget#promptConversation > QPushButton#primaryAction:disabled { background: #d5e0e6; color: #425a6a;
                border-color: #afbec8; }
            QWidget#promptConversation > QToolButton { color: #195b83; background: transparent; border: none;
                padding: 2px 4px; }
            QWidget#promptConversation > QToolButton:hover { background: #e2edf4; }
            QWidget#promptConversation > QToolButton:focus { border: 1px solid #195b83; }
            QWidget#fillPanel { background: #eaf1f6; border: 1px solid #c4d3dd; border-radius: 4px; }
            QWidget#fillPanel QLabel { color: #172b3a; }
            QWidget#fillPanel QLineEdit { background: #ffffff; color: #172b3a; border: 1px solid #9aaebc;
                border-radius: 3px; padding: 2px 4px; selection-background-color: #195b83;
                selection-color: #ffffff; }
            QWidget#fillPanel QLineEdit:focus { border: 2px solid #195b83; padding: 1px 3px; }
            QWidget#fillPanel QPushButton { background: #ffffff; color: #172b3a;
                border: 1px solid #8fa5b4; border-radius: 4px; padding: 4px 7px; }
            QWidget#fillPanel QPushButton:hover { background: #e6f0f6; }
            QWidget#fillPanel QPushButton:focus { border: 2px solid #195b83; padding: 3px 6px; }
            QWidget#fillPanel QPushButton:disabled { background: #e9eef2; color: #526a79; border-color: #afbec8; }
        """)
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
        self.conversation.setMinimumHeight(50)
        self.conversation.setAccessibleName("션과 나눈 프롬프트 대화")
        self.conversation.setPlaceholderText("요청을 적어 주시면 션이 정리를 도와드려요.")
        self.conversation.setPlainText("션: 어떤 요청을 정리할까요?\n처음에는 원하는 일을 적고, 이후에는 추가 조건을 말씀해 주세요.")

        self.provider = QComboBox()
        self.provider.setAccessibleName("정리 방식")
        self.provider.addItem("기본 정리 (AI 사용 안 함)", "offline")
        self.provider.addItem("AI 정리 · Vireonix", "vireonix")
        self.provider.addItem("AI 정리 · Pollinations Classic (예비)", "pollinations-classic")
        self.provider.setCurrentIndex(self.provider.findData("vireonix"))
        self.provider.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.provider.setMinimumContentsLength(12)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.provider_note = QLabel()
        self.provider_note.setObjectName("providerNote")
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
        self.input.setObjectName("requestInput")
        self.input.setAccessibleName("요청 또는 추가 조건")
        self._update_input_example()
        self.input.setMinimumHeight(65)
        self.input.setMaximumHeight(110)
        self.inline_notice = QLabel()
        self.inline_notice.setObjectName("inlineNotice")
        self.inline_notice.setAccessibleName("요청 오류 안내")
        self.inline_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.inline_notice.setWordWrap(True)
        self.inline_notice.hide()
        self.input.textChanged.connect(self.inline_notice.hide)
        self.send = QPushButton("션에게 보내기")
        self.send.setObjectName("primaryAction")
        self.send.clicked.connect(self.submit)
        self._other_ai_target = None
        self.retry_other = QPushButton()
        self.retry_other.setAccessibleName("다른 AI 서비스로 다시 보내기")
        self.retry_other.setToolTip("입력은 그대로 두고 다른 AI 서비스를 선택해 보냅니다. 보내기 전에 전송을 다시 확인합니다.")
        self.retry_other.clicked.connect(self._send_to_other_ai)
        self.retry_other.hide()
        self.template = QPushButton("템플릿")
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
        self.result.setMinimumHeight(60)
        self.result.setAccessibleName("정리한 프롬프트 편집")
        self.result.setPlaceholderText("정리한 결과가 여기에 표시됩니다. 직접 수정할 수 있어요.")
        self.copy = QPushButton("결과 복사")
        self.copy.clicked.connect(self._copy_result)
        self.status = _StatusText("요청을 입력하고 정리 방식을 확인해 주세요.")
        self.status.setObjectName("statusText")

        # Fill [placeholders] in the result without hunting for each bracket.
        self._fill_names = ()
        self._fill_fields = {}
        self.fill_toggle = QToolButton()
        self.fill_toggle.setCheckable(True)
        self.fill_toggle.setAccessibleName("빈칸 채우기 표시")
        self.fill_toggle.setToolTip("결과의 [대괄호] 칸에 넣을 내용을 적어 한 번에 채웁니다.")
        self.fill_toggle.toggled.connect(self._toggle_fill)
        self.fill_toggle.hide()
        self.fill_panel = QWidget()
        self.fill_panel.setObjectName("fillPanel")
        self.fill_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fill_layout = QVBoxLayout(self.fill_panel)
        fill_layout.setContentsMargins(6, 4, 6, 4)
        fill_layout.setSpacing(3)
        self.fill_form = QFormLayout()
        self.fill_form.setSpacing(3)
        fill_layout.addLayout(self.fill_form)
        self.fill_hint = QLabel()
        self.fill_hint.setWordWrap(True)
        self.fill_hint.hide()
        fill_layout.addWidget(self.fill_hint)
        fill_buttons = QHBoxLayout()
        fill_buttons.addStretch()
        self.fill_apply = QPushButton("채운 내용 넣기")
        self.fill_apply.clicked.connect(self._apply_fill)
        fill_buttons.addWidget(self.fill_apply)
        fill_layout.addLayout(fill_buttons)
        self.fill_panel.hide()
        self.result.textChanged.connect(self._refresh_fill)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(3)
        self.conversation_label = QLabel("션과 프롬프트 정리")
        self.conversation_label.setObjectName("sectionTitle")
        self.conversation_label.setBuddy(self.conversation)
        self.history_toggle = QToolButton()
        self.history_toggle.setText("대화 기록 보기")
        self.history_toggle.setCheckable(True)
        self.history_toggle.setChecked(False)
        self.history_toggle.setAccessibleName("대화 기록 표시")
        self.history_toggle.toggled.connect(self._toggle_history)
        self.conversation.hide()
        history_row = QHBoxLayout()
        history_row.addWidget(self.conversation_label)
        history_row.addStretch()
        history_row.addWidget(self.history_toggle)
        layout.addLayout(history_row)
        layout.addWidget(self.conversation, 1)
        row = QHBoxLayout()
        provider_label = QLabel("정리 방식")
        provider_label.setBuddy(self.provider)
        row.addWidget(provider_label)
        row.addWidget(self.provider, 1)
        layout.addLayout(row)
        layout.addWidget(self.provider_note)
        row = QHBoxLayout()
        task_label = QLabel("작업")
        task_label.setBuddy(self.task)
        row.addWidget(task_label)
        row.addWidget(self.task, 1)
        detail_label = QLabel("상세")
        detail_label.setBuddy(self.detail)
        row.addWidget(detail_label)
        row.addWidget(self.detail, 1)
        layout.addLayout(row)
        row = QHBoxLayout()
        input_label = QLabel("요청 · 추가 조건")
        input_label.setObjectName("sectionTitle")
        input_label.setBuddy(self.input)
        row.addWidget(input_label)
        row.addStretch()
        row.addWidget(self.template)
        row.addWidget(self.library)
        row.addWidget(self.reset)
        layout.addLayout(row)
        layout.addWidget(self.input)
        layout.addWidget(self.inline_notice)
        row = QHBoxLayout()
        row.addWidget(self.retry_other)
        row.addStretch()
        row.addWidget(self.send)
        layout.addLayout(row)
        row = QHBoxLayout()
        result_label = QLabel("정리 결과")
        result_label.setToolTip("정리 결과를 직접 수정할 수 있습니다.")
        result_label.setObjectName("sectionTitle")
        result_label.setBuddy(self.result)
        row.addWidget(result_label)
        row.addWidget(self.result_choice, 1)
        row.addWidget(self.copy)
        layout.addLayout(row)
        layout.addWidget(self.result, 3)
        layout.addWidget(self.fill_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.fill_panel)
        layout.addWidget(self.status)

    def _update_input_example(self):
        example = _FOLLOWUP_EXAMPLE if self.original else _FIRST_REQUEST_EXAMPLE
        self.input.setPlaceholderText(example)
        self.input.setAccessibleDescription(example)

    def _toggle_history(self, visible):
        self.conversation.setVisible(visible)
        self.conversation_label.setText("션과 대화" if visible else "션과 프롬프트 정리")
        self.history_toggle.setText("대화 기록 접기" if visible else "대화 기록 보기")

    def _show_notice(self, text):
        # Keep the short actionable failure by the input; full details remain
        # in the status area. The parent scrolls this into view without moving
        # keyboard focus or changing the draft.
        self.inline_notice.setText(text)
        self.inline_notice.show()
        self.notice_raised.emit()

    def _provider_changed(self):
        self._hide_other_ai()
        provider = self.provider.currentData()
        sending = "선택한 외부 서비스로 원문과 추가 조건, 이전 결과가 전송됩니다. 전송 전 다시 확인합니다."
        if provider == "offline":
            self.provider_note.setText("입력한 문장을 그대로 구조화합니다. 자연스러운 재작성은 AI 방식을 선택해 주세요.")
        elif provider == "pollinations-classic":
            self.provider_note.setText("Vireonix가 응답하지 않을 때 쓰는 예비 서비스예요. 응답에 수십 초가 걸릴 수 있고, "
                                       "보낸 뒤 30초가 지나야 다시 보낼 수 있어요. " + sending)
        else:
            self.provider_note.setText(sending)

    def _hide_other_ai(self):
        self._other_ai_target = None
        # The provider box reports its first selection before this button exists.
        button = getattr(self, "retry_other", None)
        if button is not None:
            button.hide()

    def _send_to_other_ai(self):
        target = self._other_ai_target
        if self._busy or self._sleep_paused or target is None:
            return
        # Switching hides this button; submit() then asks for consent to the
        # newly selected service before anything is sent.
        self.provider.setCurrentIndex(self.provider.findData(target))
        self.submit()

    def _refresh_fill(self):
        names = tuple(_placeholders(self.result.toPlainText()))
        if names == self._fill_names:
            return
        self._fill_names = names
        values = {name: field.text() for name, field in self._fill_fields.items()}
        while self.fill_form.rowCount():
            self.fill_form.removeRow(0)
        self._fill_fields = {}
        fillable = [name for name in names if not _is_paste_slot(name)]
        for name in fillable[:_MAX_FILL_FIELDS]:
            field = QLineEdit(values.get(name, ""))
            field.setAccessibleName(f"{name} 칸에 넣을 내용")
            field.setPlaceholderText("넣을 내용")
            field.returnPressed.connect(self._apply_fill)
            label = QLabel(name)
            label.setWordWrap(True)
            label.setBuddy(field)
            self.fill_form.addRow(label, field)
            self._fill_fields[name] = field
        hints = []
        if len(fillable) > _MAX_FILL_FIELDS:
            hints.append(f"나머지 {len(fillable) - _MAX_FILL_FIELDS}개 칸은 결과에서 직접 고쳐 주세요.")
        if any(_is_paste_slot(name) for name in names):
            hints.append("자료를 붙여 넣는 칸은 결과 편집란의 해당 자리에 직접 붙여 넣어 주세요.")
        self.fill_hint.setText(" ".join(hints))
        self.fill_hint.setVisible(bool(hints))
        self.fill_apply.setVisible(bool(self._fill_fields))
        self.fill_toggle.setVisible(bool(names))
        if not names:
            self.fill_toggle.setChecked(False)
        self._toggle_fill(self.fill_toggle.isChecked())

    def _toggle_fill(self, visible):
        count = len(self._fill_names)
        self.fill_panel.setVisible(bool(visible and count))
        self.fill_toggle.setText(f"빈칸 채우기 접기 ({count})" if visible else f"빈칸 채우기 ({count})")

    def _apply_fill(self):
        if self._busy:
            return
        text = self.result.toPlainText()
        filled = 0
        for name, field in self._fill_fields.items():
            value = field.text().strip()
            pattern = re.compile(r"\[\s*" + re.escape(name) + r"\s*\]")
            if value and pattern.search(text):
                text = pattern.sub(lambda _match: value, text)
                filled += 1
        if not filled:
            self.status.setText("넣을 내용을 적은 칸이 없어요. 칸 이름 옆에 내용을 적은 뒤 다시 눌러 주세요.")
            return
        # One edit block keeps a single Ctrl+Z step in the result editor.
        cursor = self.result.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)
        cursor.endEditBlock()
        self._save_result()
        remaining = len(_placeholders(text))
        self.status.setText(f"빈칸 {filled}개를 채웠어요. "
                            + (f"남은 빈칸은 {remaining}개예요. " if remaining else "결과를 확인한 뒤 복사해 주세요. ")
                            + "되돌리려면 결과 편집란에서 Ctrl+Z를 누르세요.")

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
            self._show_notice("정리 중에는 보관함을 불러올 수 없어요. 입력은 그대로 남아 있습니다.")
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
            self._show_notice("입력이 바뀌어 불러오지 않았어요. 기존 내용은 그대로 남아 있습니다.")
            return
        self._apply_library(item)

    def _apply_library(self, item):
        self.original = item.get("prompt", "")
        self._update_input_example()
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
            remaining = len(_placeholders(value))
            self.status.setText("결과를 복사했어요. 원문과 조건을 확인한 뒤 사용해 주세요."
                                + (f" 아직 채우지 않은 [대괄호] 칸이 {remaining}개 있어요." if remaining else ""))

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
            self._show_notice("입력이 바뀌어 새 요청을 시작하지 않았어요. 기존 내용은 그대로 남아 있습니다.")
            return
        self._clear_request()

    def _clear_request(self):
        self.original = ""
        self._update_input_example()
        self.revisions.clear()
        self.results.clear()
        self._result_revision_counts.clear()
        self._shown_result = -1
        self.result_choice.clear()
        self.result.clear()
        self.input.clear()
        self.inline_notice.hide()
        self._hide_other_ai()
        self.conversation.setPlainText("션: 어떤 요청을 정리할까요?")
        self.status.setText("새 요청을 입력해 주세요.")

    def _set_busy(self, busy):
        busy = bool(busy)
        changed = busy != self._busy
        self._busy = busy
        for control in (self.input, self.send, self.provider, self.task, self.detail, self.template, self.library,
                        self.reset, self.result, self.result_choice, self.fill_toggle, self.fill_panel):
            control.setEnabled(not busy)
        self.send.setEnabled(not busy and not self._sleep_paused)
        self.retry_other.setEnabled(not busy and not self._sleep_paused)
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

    def export_update_state(self):
        """Return the unsaved conversation for a restart, without sending it.

        The caller owns storage and must not restart on a failure.  A request,
        consent prompt or library dialog is not resumable and must finish first.
        """
        if self._busy or QApplication.activeModalWidget() is not None:
            raise RuntimeError("진행 중인 요청이나 확인창을 먼저 마쳐 주세요.")
        for name in ("_confirmation", "_library_dialog"):
            dialog = getattr(self, name, None)
            try:
                if dialog is not None and dialog.isVisible():
                    raise RuntimeError("열려 있는 확인창이나 보관함을 먼저 닫아 주세요.")
            except RuntimeError as exc:
                if "already deleted" not in str(exc):
                    raise

        template = None
        picker = getattr(self, "_template_dialog", None)
        try:
            if picker is not None and picker.isVisible():
                template = {
                    "current_id": picker._current_id,
                    "preview": picker.preview.toPlainText(),
                    "drafts": dict(picker._drafts),
                    "search": picker.search.text(),
                    "category": picker.category.currentData() or "",
                    "office_only": picker.office_button.isChecked(),
                }
        except RuntimeError as exc:
            if "already deleted" not in str(exc):
                raise

        return {
            "schema": 1,
            "original": self.original,
            "revisions": list(self.revisions),
            "results": list(self.results),
            "result_revision_counts": list(self._result_revision_counts),
            "shown_result": self._shown_result,
            "result_labels": [self.result_choice.itemText(i) for i in range(self.result_choice.count())],
            "input": self.input.toPlainText(),
            "result": self.result.toPlainText(),
            "conversation": self.conversation.toPlainText(),
            "provider": self.provider.currentData(),
            "task": self.task.currentData(),
            "detail": self.detail.currentData(),
            "history_open": self.history_toggle.isChecked(),
            "status": self.status.text(),
            "notice": self.inline_notice.text() if not self.inline_notice.isHidden() else None,
            "template": template,
        }

    def restore_update_state(self, state):
        """Restore a validated restart snapshot into an otherwise empty panel.

        Reject unknown or incompatible data instead of silently dropping it.
        The caller must keep the original snapshot if this method fails.
        """
        if self._busy or self.has_pending_work():
            raise RuntimeError("작성 중인 대화가 있어 복원하지 않았습니다.")
        if not isinstance(state, dict) or type(state.get("schema")) is not int or state["schema"] != 1:
            raise ValueError("복원할 대화의 형식을 확인할 수 없습니다.")
        text_fields = ("original", "input", "result", "conversation", "provider", "task", "detail", "status")
        if not all(isinstance(state.get(key), str) for key in text_fields):
            raise ValueError("복원할 대화에 잘못된 텍스트가 있습니다.")
        for key in ("revisions", "results", "result_labels"):
            if not isinstance(state.get(key), list) or not all(isinstance(v, str) for v in state[key]):
                raise ValueError("복원할 대화 목록이 올바르지 않습니다.")
        counts = state.get("result_revision_counts")
        if not isinstance(counts, list) or not all(type(n) is int and 0 <= n <= len(state["revisions"]) for n in counts):
            raise ValueError("복원할 결과의 조건 기록이 올바르지 않습니다.")
        shown = state.get("shown_result")
        if (len(counts) != len(state["results"]) or len(state["result_labels"]) != len(state["results"])
                or type(shown) is not int or (shown != -1 and not 0 <= shown < len(counts))
                or (counts and shown == -1) or type(state.get("history_open")) is not bool
                or (state.get("notice") is not None and not isinstance(state["notice"], str))):
            raise ValueError("복원할 결과 선택 상태가 올바르지 않습니다.")
        for control, key in ((self.provider, "provider"), (self.task, "task"), (self.detail, "detail")):
            if control.findData(state[key]) < 0:
                raise ValueError("복원할 정리 설정을 현재 버전에서 찾을 수 없습니다.")

        template = state.get("template")
        if template is not None:
            from optimizer_templates import filter_templates, load_templates
            if (not isinstance(template, dict) or not isinstance(template.get("preview"), str)
                    or not isinstance(template.get("search"), str) or not isinstance(template.get("category"), str)
                    or type(template.get("office_only")) is not bool
                    or (template.get("current_id") is not None and not isinstance(template["current_id"], str))
                    or not isinstance(template.get("drafts"), dict)
                    or not all(isinstance(k, str) and isinstance(v, str) for k, v in template["drafts"].items())):
                raise ValueError("복원할 템플릿 초안의 형식이 올바르지 않습니다.")
            catalog = load_templates()
            ids = {item["id"] for item in catalog}
            categories = {item["category"] for item in catalog}
            if (not set(template["drafts"]) <= ids
                    or (template["category"] and template["category"] not in categories)
                    or (template["current_id"] is not None and template["current_id"] not in {
                        item["id"] for item in filter_templates(catalog, template["search"], template["category"], template["office_only"])
                    })):
                raise ValueError("작성하던 템플릿을 현재 버전에서 찾을 수 없습니다.")

        self.original = state["original"]
        self.revisions = list(state["revisions"])
        self.results = list(state["results"])
        self._result_revision_counts = list(counts)
        self.result_choice.blockSignals(True)
        try:
            self.result_choice.clear()
            self.result_choice.addItems(state["result_labels"])
            self.result_choice.setCurrentIndex(shown)
        finally:
            self.result_choice.blockSignals(False)
        self._shown_result = shown
        self.result.setPlainText(state["result"])
        self.input.setPlainText(state["input"])
        self.conversation.setPlainText(state["conversation"])
        self.provider.setCurrentIndex(self.provider.findData(state["provider"]))
        self.task.setCurrentIndex(self.task.findData(state["task"]))
        self.detail.setCurrentIndex(self.detail.findData(state["detail"]))
        self.history_toggle.setChecked(state["history_open"])
        self.status.setText(state["status"])
        if state["notice"] is not None:
            self.inline_notice.setText(state["notice"])
            self.inline_notice.show()
        else:
            self.inline_notice.hide()
        self._update_input_example()

        if template is not None:
            self._insert_template()
            picker = self._template_dialog
            picker.office_button.setChecked(template["office_only"])
            picker.category.setCurrentIndex(max(0, picker.category.findData(template["category"])))
            picker.search.setText(template["search"])
            picker._drafts = dict(template["drafts"])
            if template["current_id"] is not None:
                for row in range(picker.list.count()):
                    item = picker.list.item(row)
                    if item.data(Qt.ItemDataRole.UserRole)["id"] == template["current_id"]:
                        picker.list.setCurrentRow(row)
                        break
            picker.preview.setPlainText(template["preview"])
            picker._drafts = dict(template["drafts"])

    def set_sleep_paused(self, paused: bool):
        """Pause new requests without cancelling a request already in flight."""
        self._sleep_paused = bool(paused)
        self.send.setEnabled(not self._busy and not self._sleep_paused)
        self.retry_other.setEnabled(not self._busy and not self._sleep_paused)

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
            self._show_notice("먼저 요청 또는 추가 조건을 입력해 주세요.")
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
            self._show_notice("입력이나 설정이 바뀌어 전송하지 않았어요. 다시 확인해 주세요.")
            return
        self._submit_confirmed(*args)

    def _submit_confirmed(self, text, original, revision, provider, old_context, next_context, previous, task, detail, pending_revisions):
        # The pet may have been put to sleep while the confirmation was open.
        if self._sleep_paused:
            self.status.setText("션이 완전히 자고 있어요. 깨운 후 다시 보내 주세요. 입력은 그대로 남아 있습니다.")
            self._show_notice("션을 깨운 후 다시 보내 주세요. 입력은 그대로 남아 있습니다.")
            return
        # A second confirmation can return after another request has started.
        if self._busy:
            self.status.setText("이미 정리 중이에요. 입력은 그대로 남아 있습니다.")
            self._show_notice("이미 정리 중이에요. 입력은 그대로 남아 있습니다.")
            return
        self._pending = (text, original, revision, provider)
        self.inline_notice.hide()
        self._hide_other_ai()
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
        # The result editor is usable before the first generation. Keep an
        # unslotted draft as a selectable entry instead of overwriting it.
        # It is deliberately not included in a first external AI request.
        unslotted_draft = self._shown_result < 0 and self.result.toPlainText() != ""
        if unslotted_draft:
            self.results.append(self.result.toPlainText())
            self._result_revision_counts.append(0)
            self.result_choice.addItem("직접 작성한 결과")
        self.original = original
        self._update_input_example()
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
        if unslotted_draft:
            notes.append("미리 작성한 결과는 ‘직접 작성한 결과’에서 확인할 수 있어요.")
        self.status.setText("\n".join(notes) if notes else "원래 의도와 조건을 확인해 주세요.")
        self._set_busy(False)
        self.input.setFocus()
        self.result_ready.emit()

    def _fail(self, message):
        self._set_busy(False)
        status = "정리하지 못했어요: " + message + "\n입력과 이전 결과는 그대로 남아 있습니다."
        provider = self._pending[3]
        if provider in _OTHER_AI:
            target, label = _OTHER_AI[provider]
            status += ("\n" + ("션을 깨운 뒤 " if self._sleep_paused else "") +
                       "다시 시도하려면 ‘션에게 보내기’를 누르세요. "
                       f"‘{label}’ 버튼으로 다른 AI 서비스에 보내거나 기본 정리를 선택할 수도 있어요. "
                       "AI로 보낼 때마다 전송 여부를 확인하며, 기본 정리는 AI 재작성이 아닙니다.")
            self._other_ai_target = target
            self.retry_other.setText(label)
            self.retry_other.setEnabled(not self._sleep_paused)
            self.retry_other.show()
        elif provider != "offline":
            status += ("\n다시 시도하려면 ‘션에게 보내기’를 누르세요. "
                       "AI로 보낼 때마다 전송 여부를 확인하며, 기본 정리는 AI 재작성이 아닙니다.")
        self.status.setText(status)
        short_message = message if len(message) <= 180 else message[:177] + "…"
        self._show_notice("정리하지 못했어요: " + short_message)
