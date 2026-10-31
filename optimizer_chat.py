"""Conversational prompt drafting, hosted inside the pet's existing window."""
from __future__ import annotations

import re
import threading
import time

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Signal, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLayout, QLineEdit, QMessageBox, QPushButton, QSizePolicy,
                               QSpacerItem, QTextEdit, QToolButton, QVBoxLayout, QWidget)

from optimizer_core import TASKS, create_offline_prompt, review_result, validate_input
from optimizer_ai import optimize_ai

_FIRST_REQUEST_EXAMPLE = "예: 연수 안내문 작성, 날짜는 [확인 필요]"
_FOLLOWUP_EXAMPLE = "예: 위 내용을 세 항목으로 나눠 주세요."
_PLACEHOLDER = re.compile(r"\[([^\[\]\n]{2,40})\]")
_MAX_FILL_FIELDS = 8
# After a failed AI request, offer the other fixed service. Sending still goes
# through the normal per-request transmission confirmation.
_OTHER_AI = {"vireonix": ("pollinations-classic", "Pollinations Classic으로 보내기"),
             "pollinations-classic": ("vireonix", "Vireonix로 보내기"),
             "jarvisclaw": ("pollinations-classic", "Pollinations Classic으로 보내기")}
_JARVIS_NOTICE = ("JarvisClaw는 외부 모델 제공자에게 요청을 전달할 수 있고 일부 이용 메타데이터를 "
                  "처리할 수 있습니다. 개인정보와 비공개 자료를 보내지 마세요. "
                  "기관 사용 승인을 확인해 주세요. 개인정보 처리방침: https://api.jarvisclaw.ai/privacy-policy")


def _safe_generation_error(exc):
    """Show only known fixed errors; provider exceptions can contain private data."""
    message = str(exc)
    known = {
        "원래 요청을 입력해 주세요.", "원래 요청은 30,000자 이내로 입력해 주세요.",
        "맥락은 2,000자 이내의 텍스트로 입력해 주세요.", "작업 유형 또는 상세 수준을 확인해 주세요.",
        "이전 정리 결과가 비어 있습니다.", "AI 프로필이나 인증 정보가 바뀌었습니다. 비교를 다시 확인하세요.",
        "원래 요청은 1~30,000자 이내로 입력해 주세요.", "맥락은 2,000자 이내로 입력해 주세요.",
        "수정 요청은 2,000자 이내로 입력해 주세요.", "이전 결과는 30,000자 이내여야 합니다.",
        "작업 유형을 확인해 주세요.", "상세 수준을 확인해 주세요.", "설정한 AI 정리 방식을 선택해 주세요.",
        "모델 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.", "모델 응답이 너무 큽니다.",
        "AI 응답이 길이 제한으로 중간에 잘렸습니다. 요청을 줄여 다시 시도해 주세요.",
        "AI 서비스의 콘텐츠 필터로 응답이 제한되었습니다.", "모델 응답이 비어 있습니다.",
        "모델 응답을 읽을 수 없습니다. 잠시 후 다시 시도해 주세요.",
        "AI 서비스 주소 확인이 진행 중입니다. 잠시 후 다시 시도해 주세요.",
        "AI 서비스 주소 확인 시간이 초과되었습니다. 인터넷 연결과 DNS 설정을 확인하고 다시 시도해 주세요.",
        "AI 서비스 주소를 확인하지 못했습니다. 인터넷 연결과 DNS 설정을 확인하고 다시 시도해 주세요.",
        "사설망 또는 확인할 수 없는 AI 서비스 주소는 사용할 수 없습니다.",
        "AI 서비스 주소를 확인하지 못했습니다.", "AI 서비스 주소를 확인해 주세요.",
        "AI 서비스에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요.", "정리 요청을 취소했습니다.",
        "선택한 AI의 연결 설정을 다시 확인해 주세요.",
        "기존 정리 방식에는 별도 AI 연결 설정을 사용하지 않습니다.",
        "Pollinations Classic 요청이 진행 중입니다. 완료 후 다시 시도해 주세요.",
        "서비스 사용량 제한에 도달했습니다 (HTTP 429). Vireonix는 같은 인터넷 연결을 쓰는 사용자들이 시간당 한도를 함께 씁니다. 잠시 후 다시 시도해 주세요.",
        "서비스 사용량 제한에 도달했습니다 (HTTP 429). 잠시 후 다시 시도해 주세요.",
        "Pollinations Classic의 요청 간격이나 이용 한도 문제일 수 있습니다 (HTTP 402).",
        "JarvisClaw 무료 요청을 처리할 수 없습니다 (HTTP 402). 잠시 후 다시 시도해 주세요.",
        "ChatGPT 요청이 취소되었습니다.", "ChatGPT 계정 연결을 확인하고 필요하면 다시 연결해 주세요.",
        "ChatGPT 사용이 현재 제한되어 있습니다. 잠시 후 다시 확인해 주세요.",
        "ChatGPT 연결에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
        "ChatGPT 응답을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.",
    }
    if message in known:
        return message
    if re.fullmatch(r"AI 서비스가 일시적으로 응답하지 않습니다 \(HTTP 50[234]\)\. 잠시 후 다시 시도해 주세요\.", message):
        return message
    if re.fullmatch(r"AI 요청에 실패했습니다 \(HTTP [1-5][0-9]{2}\)\. 잠시 후 다시 시도해 주세요\.", message):
        return message
    if re.fullmatch(r"Pollinations Classic 요청 간격을 지키기 위해 (?:[1-9]|[12][0-9]|30)초 후 다시 시도해 주세요\.", message):
        return message
    return "연결 실패 또는 정리 중 오류가 발생했습니다. 설정과 인터넷 연결을 확인하고 다시 시도해 주세요."


class _ActionRow(QLayout):
    """Wrap existing controls at their native size hints, without a new widget."""
    def __init__(self):
        super().__init__()
        self._items = []
        self._stretches = {}
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(3)

    def addItem(self, item):
        self._items.append(item)

    def addWidget(self, widget, stretch=0):
        super().addWidget(widget)
        self._stretches[self._items[-1]] = stretch

    def addStretch(self):
        item = QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.addItem(item)
        self._stretches[item] = 1

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self._stretches.pop(item, None)
            return item
        return None

    def expandingDirections(self):
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), False)

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        return size

    def sizeHint(self):
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def _arrange(self, rect, apply):
        rows, row, used = [], [], 0
        spacing = self.spacing()
        for item in self._items:
            if item.widget() is not None and item.isEmpty():
                continue
            hint = item.sizeHint().expandedTo(item.minimumSize())
            width = hint.width()
            if row and used + spacing + width > rect.width():
                rows.append(row)
                row, used = [], 0
            row.append((item, width, hint.height()))
            used += width + (spacing if len(row) > 1 else 0)
        if row:
            rows.append(row)
        y = rect.y()
        for row in rows:
            height = max(entry[2] for entry in row)
            total = sum(entry[1] for entry in row) + spacing * (len(row) - 1)
            extra = max(0, rect.width() - total)
            stretches = sum(self._stretches.get(entry[0], 0) for entry in row)
            x = rect.x()
            for item, width, _ in row:
                if stretches:
                    width += extra * self._stretches.get(item, 0) // stretches
                if apply:
                    item.setGeometry(QRect(QPoint(x, y), QSize(width, height)))
                x += width + spacing
            y += height + spacing
        return max(0, y - rect.y() - spacing)


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
        self.setTabChangesFocus(True)
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
    task_feedback = Signal(str)

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
        # Request-time review inputs, kept in memory without changing stored schemas.
        self._result_review_inputs: list[tuple[str, str] | None] = []
        self._pending_review_input: tuple[str, str] | None = None
        self._shown_result = -1
        self._busy = False
        self._sleep_paused = False
        self._consent_epoch = 0
        self._clarifications = ""
        self._comparison_active = False
        self._comparison_signals = _ReplySignals(self)
        self._comparison_signals.completed.connect(self._comparison_event)
        self._signals = _ReplySignals(self)
        self._signals.completed.connect(self._finish)
        self._signals.failed.connect(self._fail)

        self.conversation = QTextEdit()
        self.conversation.setReadOnly(True)
        self.conversation.setTabChangesFocus(True)
        self.conversation.setMinimumHeight(50)
        self.conversation.setAccessibleName("션과 나눈 프롬프트 대화")
        self.conversation.setPlaceholderText("요청을 적어 주시면 션이 정리를 도와드려요.")
        self.conversation.setPlainText("션: 어떤 요청을 정리할까요?\n처음에는 원하는 일을 적고, 이후에는 추가 조건을 말씀해 주세요.")

        self.provider = QComboBox()
        self.provider.setAccessibleName("정리 방식")
        self.provider.addItem("기본 정리 (AI 사용 안 함)", "offline")
        self.provider.addItem("AI 정리 · Vireonix", "vireonix")
        self.provider.addItem("AI 정리 · Pollinations Classic (예비)", "pollinations-classic")
        self.provider.addItem("AI 정리 · JarvisClaw (무료)", "jarvisclaw")
        self.provider.setCurrentIndex(self.provider.findData("jarvisclaw"))
        self.reload_profiles()
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
        self.input.setToolTip("Enter: 줄바꿈 · Ctrl+Enter: 보내기")
        self.input.setTabChangesFocus(True)
        self.input.installEventFilter(self)
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
        self.ai_settings = QPushButton("AI 설정")
        self.ai_settings.setAccessibleName("AI 설정 열기")
        self.ai_settings.clicked.connect(self._open_ai_settings)
        self.clarify = QPushButton("조건 확인")
        self.clarify.setAccessibleName("빠진 조건 확인")
        self.clarify.clicked.connect(self._open_clarification)
        self.compare = QPushButton("두 모델 비교")
        self.compare.setAccessibleName("두 모델 비교")
        self.compare.clicked.connect(self._open_comparison)
        self.diff = QPushButton("원문 차이")
        self.diff.setAccessibleName("원문과 결과 문자 차이")
        self.diff.clicked.connect(self._open_diff)
        self.tools_toggle = QToolButton()
        self.tools_toggle.setCheckable(True)
        self.tools_toggle.setText("도구 더보기")
        self.tools_toggle.setAccessibleName("추가 도구 표시")
        self.tools_toggle.setToolTip("조건 확인, 두 모델 비교, 원문 차이를 표시합니다.")
        self.tools_toggle.toggled.connect(self._toggle_tools)
        for tool in (self.clarify, self.compare, self.diff):
            tool.hide()

        self.result_choice = QComboBox()
        self.result_choice.setAccessibleName("이전 정리 결과 선택")
        self.result_choice.currentIndexChanged.connect(self._select_result)
        self.result = QTextEdit()
        self.result.setTabChangesFocus(True)
        self.result.setMinimumHeight(60)
        self.result.setAccessibleName("정리한 프롬프트 편집")
        self.result.setPlaceholderText("정리한 결과가 여기에 표시됩니다. 직접 수정할 수 있어요.")
        self.copy = QPushButton("결과 복사")
        self.copy.clicked.connect(self._copy_result)
        self.result.textChanged.connect(self._reset_copy)
        # activated also covers selecting the already-current history item.
        self.result_choice.activated.connect(self._reset_copy)
        self.status = _StatusText("요청을 입력하고 정리 방식을 확인해 주세요.")
        self.status.setObjectName("statusText")
        self.result_metadata = QLabel()
        self.result_metadata.setAccessibleName("선택 결과 모델 정보")
        self.result_metadata.setWordWrap(True)

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
        self.result.textChanged.connect(self._refresh_quality)

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
        history_row = _ActionRow()
        history_row.addWidget(self.conversation_label)
        history_row.addStretch()
        history_row.addWidget(self.history_toggle)
        layout.addLayout(history_row)
        layout.addWidget(self.conversation, 1)
        row = _ActionRow()
        provider_label = QLabel("정리 방식")
        provider_label.setBuddy(self.provider)
        row.addWidget(provider_label)
        row.addWidget(self.provider, 1)
        row.addWidget(self.ai_settings)
        layout.addLayout(row)
        layout.addWidget(self.provider_note)
        row = _ActionRow()
        task_label = QLabel("작업")
        task_label.setBuddy(self.task)
        row.addWidget(task_label)
        row.addWidget(self.task, 1)
        detail_label = QLabel("상세")
        detail_label.setBuddy(self.detail)
        row.addWidget(detail_label)
        row.addWidget(self.detail, 1)
        layout.addLayout(row)
        row = _ActionRow()
        input_label = QLabel("1. 요청 · 추가 조건")
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
        row = _ActionRow()
        row.addWidget(self.retry_other)
        row.addWidget(self.tools_toggle)
        row.addStretch()
        row.addWidget(self.send)
        layout.addLayout(row)
        row = _ActionRow()
        row.addWidget(self.clarify)
        row.addWidget(self.compare)
        row.addWidget(self.diff)
        layout.addLayout(row)
        row = _ActionRow()
        result_label = QLabel("2. 정리 결과")
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
        layout.addWidget(self.result_metadata)
        layout.addWidget(self.status)

        # Measure both labels after the existing parent stylesheet is applied.
        self.ensurePolished()
        copy_width = self.copy.sizeHint().width()
        self.copy.setText("복사됨")
        self.copy.setFixedWidth(max(copy_width, self.copy.sizeHint().width()))
        self._reset_copy()
        controls = (self.history_toggle, self.provider, self.ai_settings, self.task, self.detail,
                    self.template, self.library, self.reset, self.input, self.send, self.retry_other,
                    self.tools_toggle, self.clarify, self.compare, self.diff, self.result_choice,
                    self.copy, self.result, self.fill_toggle, self.status)
        for before, after in zip(controls, controls[1:]):
            QWidget.setTabOrder(before, after)

    def eventFilter(self, watched, event):
        if (watched is self.input and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() in (Qt.KeyboardModifier.ControlModifier,
                                          Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.KeypadModifier)):
            if not event.isAutoRepeat():
                self.submit()
            return True
        return super().eventFilter(watched, event)

    def _update_input_example(self):
        example = _FOLLOWUP_EXAMPLE if self.original else _FIRST_REQUEST_EXAMPLE
        self.input.setPlaceholderText(example)
        self.input.setAccessibleDescription(example)

    def _toggle_history(self, visible):
        self.conversation.setVisible(visible)
        self.conversation_label.setText("션과 대화" if visible else "션과 프롬프트 정리")
        self.history_toggle.setText("대화 기록 접기" if visible else "대화 기록 보기")

    def _toggle_tools(self, visible):
        if not visible and any(tool.hasFocus() for tool in (self.clarify, self.compare, self.diff)):
            self.tools_toggle.setFocus()
        for tool in (self.clarify, self.compare, self.diff):
            tool.setVisible(visible)
        self.tools_toggle.setText("도구 접기" if visible else "도구 더보기")
        self.tools_toggle.setAccessibleName("추가 도구 접기" if visible else "추가 도구 표시")

    def _show_notice(self, text):
        # Keep the short actionable failure by the input; full details remain
        # in the status area. The parent scrolls this into view without moving
        # keyboard focus or changing the draft.
        self.inline_notice.setText(text)
        self.inline_notice.show()
        self.notice_raised.emit()

    def _provider_changed(self):
        self._consent_epoch += 1
        self._hide_other_ai()
        provider = self.provider.currentData()
        sending = "선택한 외부 서비스로 원문과 추가 조건, 이전 결과가 전송됩니다. 전송 전 다시 확인합니다."
        if provider == "offline":
            self.provider_note.setText("입력한 문장을 그대로 구조화합니다. 자연스러운 재작성은 AI 방식을 선택해 주세요.")
        elif provider == "pollinations-classic":
            self.provider_note.setText("Vireonix가 응답하지 않을 때 쓰는 예비 서비스예요. 응답에 수십 초가 걸릴 수 있고, "
                                       "보낸 뒤 30초가 지나야 다시 보낼 수 있어요. " + sending)
        elif provider == "jarvisclaw":
            self.provider_note.setText("로그인과 키 없이 외부 무료 AI를 사용합니다. 가용성과 모델이 바뀔 수 있습니다. "
                                       "개인정보·비공개 자료는 보내지 마세요. " + sending)
        elif str(provider).startswith("profile:"):
            try:
                profile = self._resolve_profile(provider)
            except ValueError as exc:
                self.provider_note.setText(str(exc))
                return
            if profile.provider == "ollama":
                self.provider_note.setText("선택한 Ollama 로컬 서버에서 처리합니다. 서버의 클라우드 비활성화 설정을 확인해 주세요.")
            elif profile.provider == "chatgpt-subscription":
                self.provider_note.setText("연결한 ChatGPT 계정의 구독 한도를 사용합니다. 전송 전 내용을 다시 확인합니다.")
            else:
                self.provider_note.setText(sending)
        else:
            self.provider_note.setText(sending)

    def reload_profiles(self):
        """Keep the legacy defaults and append explicitly configured profiles."""
        from ai_profiles import load_profiles
        selected = self.provider.currentData()
        for index in range(self.provider.count() - 1, -1, -1):
            if str(self.provider.itemData(index)).startswith("profile:"):
                self.provider.removeItem(index)
        for profile in load_profiles():
            self.provider.addItem(f"{profile.provider} / {profile.model}", "profile:" + profile.id)
        self.provider.setCurrentIndex(max(0, self.provider.findData(selected)))

    def _resolve_profile(self, destination):
        if not str(destination).startswith("profile:"):
            return None
        from ai_profiles import load_profiles, validate_profile
        profile = next((p for p in load_profiles() if "profile:" + p.id == destination), None)
        if profile is None:
            raise ValueError("선택한 AI 프로필을 찾을 수 없습니다.")
        validate_profile(profile)
        return profile

    def _destination(self, destination):
        profile = self._resolve_profile(destination)
        if profile:
            location = "로컬" if profile.provider == "ollama" else "외부"
            return f"{profile.provider} / {profile.model} ({location})"
        return {
            "offline": "기본 정리 / local (로컬)", "vireonix": "Vireonix / auto",
            "pollinations-classic": "Pollinations Classic / openai",
            "jarvisclaw": "JarvisClaw / auto/free"}[destination]

    def _profile_snapshot(self):
        from ai_profiles import load_profiles
        from ai_secrets import get_secret
        import chatgpt_auth
        profiles = tuple(load_profiles())
        revisions = []
        for profile in profiles:
            credential = None
            if profile.account_id:
                account = chatgpt_auth.account_status(profile.account_id)
                credential = (account.connected, account.credential_revision)
            elif profile.provider not in ("ollama", "chatgpt-subscription"):
                # A process-local fingerprint is never persisted or displayed.
                credential = hash(repr(get_secret("provider:" + profile.provider)))
            revisions.append((profile, credential))
        return tuple(revisions)

    def _open_comparison(self):
        if self._busy or self._sleep_paused:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("두 모델 비교")
        layout = QVBoxLayout(dialog)
        choices = []
        for label in ("비교 모델 A", "비교 모델 B"):
            combo = QComboBox()
            combo.setAccessibleName(label)
            for index in range(self.provider.count()):
                combo.addItem(self.provider.itemText(index), self.provider.itemData(index))
            layout.addWidget(combo)
            choices.append(combo)
        if choices[1].count() > 1:
            choices[1].setCurrentIndex(1)
        start = QPushButton("두 요청 확인")
        start.setAccessibleName("비교 요청 확인")
        layout.addWidget(start)

        def begin():
            destinations = [combo.currentData() for combo in choices]
            try:
                pairs = []
                for destination in destinations:
                    profile = self._resolve_profile(destination)
                    pairs.append((profile.id, profile.model, profile.account_id) if profile
                                 else (destination, "", None))
                if pairs[0] == pairs[1]:
                    raise ValueError("서로 다른 모델 또는 프로필을 선택하세요.")
                text = self.input.toPlainText()
                if not text.strip():
                    raise ValueError("먼저 요청 또는 추가 조건을 입력하세요.")
                labels = [self._destination(value) for value in destinations]
                profiles = [self._resolve_profile(value) for value in destinations]
                expected = self._request_snapshot()
                frozen = self._generation_input(text)
            except ValueError as exc:
                self._show_notice(str(exc))
                return
            dialog.accept()
            body = ("같은 입력으로 순서대로 2회 요청합니다.\nA: " + labels[0] + "\nB: " + labels[1]
                    + "\n로컬 정리와 외부 전송을 구분해 확인하세요. B에는 A의 결과를 보내지 않습니다.")
            if "jarvisclaw" in destinations:
                body += "\n\n" + _JARVIS_NOTICE
            confirmation = self._confirm("비교 요청 확인", body,
                lambda: self._start_comparison(expected, frozen, destinations, profiles))
            if isinstance(confirmation, QMessageBox):
                confirmation.setDetailedText(
                    f"원문:\n{frozen[1]}\n\n누적 조건:\n{frozen[3]}\n\n추가 조건:\n{frozen[2]}"
                    f"\n\n이전 결과:\n{frozen[5]}\n\n작업: {frozen[6]}\n상세: {frozen[7]}")
                confirmation.finished.connect(lambda _: confirmation.setDetailedText(""))
        start.clicked.connect(begin)
        self._comparison_dialog = dialog
        self._comparison_choices = choices
        dialog.open()

    def _generation_input(self, text):
        original = self.original or text
        revision = text if self.original else ""
        previous = self.result.toPlainText() if self.original else ""
        old_context = "\n\n".join(filter(None, [self._clarifications, *self.revisions]))
        next_context = "\n\n".join(filter(None, [old_context, revision]))
        count = (self._result_revision_counts[self._shown_result]
                 if 0 <= self._shown_result < len(self._result_revision_counts) else 0)
        pending = [*self.revisions[count:], revision] if revision else []
        return (text, original, revision, old_context, next_context, previous,
                self.task.currentData(), self.detail.currentData(), pending)

    @staticmethod
    def _generate(frozen, destination, profile=None):
        text, original, revision, old_context, next_context, previous, task, detail, pending = frozen
        started = time.monotonic()
        if destination == "offline":
            if revision:
                validate_input(original, next_context, task, detail)
                if not previous.strip():
                    raise ValueError("이전 정리 결과가 비어 있습니다.")
                reply = {"result": _append_conditions(previous, pending), "notes": [],
                         "mode": "offline"}
            else:
                reply = create_offline_prompt(original, context=next_context, task=task, detail=detail)
        else:
            kwargs = dict(context=old_context, provider=profile.provider if profile else destination,
                          revision=revision, previous_result=previous, task=task, detail=detail)
            if profile is not None:
                kwargs["profile"] = profile
            reply = optimize_ai(original, **kwargs)
        reply = dict(reply)
        reply["_metadata"] = {"provider": profile.provider if profile else destination,
                              "model": profile.model if profile else {
                                  "offline": "local", "vireonix": "auto",
                                  "pollinations-classic": "openai", "jarvisclaw": "auto/free"}[destination],
                              "elapsed": max(0.0, time.monotonic() - started)}
        return reply

    def _start_comparison(self, expected, frozen, destinations, profiles):
        if self._busy or self._sleep_paused or expected != self._request_snapshot():
            self._show_notice("입력이나 설정이 바뀌었습니다. 비교 요청을 다시 확인하세요.")
            return
        self._comparison_active = True
        self._comparison_frozen = frozen
        self._comparison_destinations = destinations
        self._pending = (frozen[0], frozen[1], frozen[2], destinations[0])
        self._pending_review_input = (frozen[1], frozen[4])
        self._set_busy(True)
        self.status.setText("두 모델을 순서대로 요청하고 있어요. 응답에 1분 이상 걸릴 수 있습니다. "
                            "입력과 이전 결과는 보존됩니다.")

        def work():
            for index, (destination, profile) in enumerate(zip(destinations, profiles)):
                try:
                    if self._profile_snapshot() != expected[-1]:
                        raise ValueError("AI 프로필이나 인증 정보가 바뀌었습니다. 비교를 다시 확인하세요.")
                    reply = self._generate(frozen, destination, profile)
                except Exception as exc:
                    self._comparison_signals.completed.emit(("error", index, _safe_generation_error(exc)))
                    return
                self._comparison_signals.completed.emit(("result", index, reply))
            self._comparison_signals.completed.emit(("done", 1, None))
        try:
            threading.Thread(target=work, daemon=True, name="prompt-comparison").start()
        except RuntimeError:
            self._comparison_event(("error", 0, "비교 작업을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요."))

    def _comparison_event(self, event):
        kind, index, value = event
        if kind == "result":
            frozen = self._comparison_frozen
            self._comparison_index = index
            self._pending = (frozen[0], frozen[1], frozen[2] if index == 0 else "",
                             self._comparison_destinations[index])
            self._finish(value)
            if index == 0:
                first_status = self.status.text()
                self.status.setText("첫 결과를 받았어요. 두 번째 모델의 응답을 기다리는 중입니다. "
                                    "응답에 1분 이상 걸릴 수 있습니다.\n" + first_status)
        else:
            self._comparison_active = False
            if kind == "error":
                self._pending = (*self._pending[:3], self._comparison_destinations[index])
                self._fail(value)
                review = self._selected_review()
                if review:
                    self.status.setText(self.status.text() + "\n현재 선택한 결과 확인:\n" + "\n".join(review))
            else:
                self._set_busy(False)

    def _open_ai_settings(self):
        if self._busy:
            return
        from ai_settings import AISettingsDialog
        dialog = AISettingsDialog(self)
        dialog.profiles_changed.connect(self.reload_profiles)
        self._settings_dialog = dialog
        dialog.open()

    def _open_clarification(self):
        if self._busy:
            return
        from prompt_quality import missing_conditions
        text = self.original or self.input.toPlainText()
        fields = missing_conditions(text, self._clarifications)
        dialog = QDialog(self)
        dialog.setWindowTitle("빠진 조건 확인 (선택)")
        dialog.setAccessibleName("빠진 조건 확인")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        answers = {}
        for item in fields:
            field = QLineEdit()
            if item["field"] == "inquiry_roles":
                field.setAccessibleName("문의자·답변자·수신자 (선택)")
                field.setPlaceholderText("문의자: … / 답변자: … / 수신자: … 또는 미정")
                field.setToolTip("누가 문의하고 누가 누구에게 답변하는지 적어 주세요. 정해지지 않았다면 미정이라고 적을 수 있습니다.")
            else:
                field.setAccessibleName("조건 " + item["field"])
            answers[item["field"]] = field
            form.addRow(item["question"], field)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply |
                                    QDialogButtonBox.StandardButton.Cancel)
        skip = buttons.addButton("건너뛰기", QDialogButtonBox.ButtonRole.RejectRole)
        skip.setAccessibleName("조건 확인 건너뛰기")
        expected = self._request_snapshot()

        def apply():
            if self._request_snapshot() != expected:
                self._show_notice("입력이 바뀌었습니다. 조건을 다시 확인해 주세요.")
                dialog.reject()
                return
            additions = [f"{key}: {field.text().strip()}" for key, field in answers.items()
                         if field.text().strip()]
            if additions:
                self._clarifications = "\n".join(filter(None, [self._clarifications, *additions]))
            dialog.accept()
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(apply)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self._clarification_dialog = dialog
        self._clarification_fields = answers
        dialog.open()

    def _quality_text(self):
        from prompt_quality import condition_report, literal_diff
        original = self.original or self.input.toPlainText()
        result = self.result.toPlainText()
        context = "\n\n".join(filter(None, [self._clarifications, *self.revisions]))
        changes = literal_diff(original, result)
        findings = condition_report(original, context, result)
        change_labels = {"insert": "추가", "delete": "삭제", "replace": "변경"}
        condition_labels = {"number": "수량·숫자", "date": "날짜", "time": "시간", "weekday": "요일",
                            "quote": "인용 문구", "prohibition": "금지 조건"}
        status_labels = {"present": "문자 일치", "missing": "문자 일치 없음"}
        source_labels = {"original": "원문", "context": "추가 조건"}
        spans = []
        for change in changes:
            if change["tag"] == "equal":
                continue
            before = original[change["before_start"]:change["before_end"]]
            after = result[change["after_start"]:change["after_end"]]
            spans.append(f'{change_labels.get(change["tag"], "변경")} · 원문 {change["before_start"]}:{change["before_end"]}'
                         f' → 결과 {change["after_start"]}:{change["after_end"]}\n'
                         f'- {before}\n+ {after}')
        return ("문자 단위 비교이며 의미 보존을 보장하지 않습니다.\n\n"
                + "\n\n".join(spans) + "\n\n조건 확인:\n"
                + ("\n".join(f'{condition_labels.get(item["kind"], "기타")}: {item["literal"]} · '
                              f'{status_labels.get(item["status"], "확인 필요")} '
                              f'({source_labels.get(item["source"], "출처 확인 필요")})'
                              for item in findings) or "문자로 확인할 조건이 없습니다."))

    def _open_diff(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("원문과 결과 문자 차이")
        dialog.resize(600, 450)
        layout = QVBoxLayout(dialog)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setAccessibleName("문자 차이와 조건 확인")
        layout.addWidget(view)
        self._diff_dialog, self._diff_view = dialog, view
        self._refresh_quality()
        dialog.open()

    def _refresh_quality(self):
        view = getattr(self, "_diff_view", None)
        if view is not None:
            view.setPlainText(self._quality_text())

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
                                             revisions=([self._clarifications] if self._clarifications else [])
                                             + list(self.revisions))
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
        self._reset_copy()
        self._clarifications = ""
        self.original = item.get("prompt", "")
        self._update_input_example()
        self.task.setCurrentIndex(max(0, self.task.findData(item.get("task", "auto"))))
        self.detail.setCurrentIndex(max(0, self.detail.findData(item.get("detail", "balanced"))))
        self.revisions = list(item.get("syeonRevisions", []))
        self.results = [item.get("result", "")]
        self._result_revision_counts = [len(self.revisions)]
        self._result_review_inputs = [None]
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
        self._reset_copy()
        self._save_result()
        self._shown_result = index
        self.result.setPlainText(self.results[index] if 0 <= index < len(self.results) else "")
        self._refresh_metadata()
        if not self._busy and 0 <= index < len(self.results):
            review = self._selected_review()
            self.status.setText("\n".join(review) if review else "선택한 결과의 원문과 조건을 확인해 주세요.")

    def _selected_review(self):
        index = self._shown_result
        if 0 <= index < len(self._result_review_inputs):
            source = self._result_review_inputs[index]
            if source is not None:
                return review_result(source[0], source[1], self.result.toPlainText())
        return []

    def _refresh_metadata(self):
        metadata = self.result_choice.itemData(self._shown_result)
        if metadata:
            self.result_metadata.setText(
                f'{metadata["provider"]} / {metadata["model"]} · {metadata["elapsed"]:.2f}초')
        else:
            self.result_metadata.clear()

    def _reset_copy(self):
        self.copy.setText("결과 복사")

    def _copy_result(self):
        value = self.result.toPlainText()
        if value:
            self._reset_copy()
            clipboard = QApplication.clipboard()
            try:
                clipboard.setText(value)
                copied = clipboard.text().replace("\r\n", "\n") == value.replace("\r\n", "\n")
            except RuntimeError:
                copied = False
            if not copied:
                self.status.setText("복사하지 못했어요. 다시 시도해 주세요.")
                return
            self.copy.setText("복사됨")
            remaining = len(_placeholders(value))
            self.status.setText("결과를 복사했어요. 원문과 조건을 확인한 뒤 사용해 주세요."
                                + (f" 아직 채우지 않은 [대괄호] 칸이 {remaining}개 있어요." if remaining else ""))
            self.task_feedback.emit("copied")

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
                self.input.toPlainText(), self.result.toPlainText(), self._clarifications)

    def _clear_if_unchanged(self, expected):
        if self._busy or self._draft_snapshot() != expected:
            self.status.setText("확인 중 입력이 바뀌어 새 요청을 시작하지 않았어요. 기존 내용은 그대로 남아 있습니다.")
            self._show_notice("입력이 바뀌어 새 요청을 시작하지 않았어요. 기존 내용은 그대로 남아 있습니다.")
            return
        self._clear_request()

    def _clear_request(self):
        self._reset_copy()
        self.original = ""
        self._clarifications = ""
        self._update_input_example()
        self.revisions.clear()
        self.results.clear()
        self._result_revision_counts.clear()
        self._result_review_inputs.clear()
        self._pending_review_input = None
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
                        self.reset, self.result, self.result_choice, self.fill_toggle, self.fill_panel,
                        self.ai_settings, self.clarify, self.compare):
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
                    or self._clarifications
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
        for name in ("_confirmation", "_library_dialog", "_settings_dialog", "_comparison_dialog"):
            dialog = getattr(self, name, None)
            try:
                if name == "_settings_dialog" and dialog is not None and dialog.is_busy:
                    raise RuntimeError("AI 설정 작업을 먼저 마쳐 주세요.")
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
            "clarifications": self._clarifications,
            "result_metadata": [self.result_choice.itemData(i) for i in range(self.result_choice.count())],
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
        if not isinstance(state.get("clarifications", ""), str):
            raise ValueError("복원할 추가 조건이 올바르지 않습니다.")
        metadata = state.get("result_metadata")
        if metadata is not None and (
                not isinstance(metadata, list) or len(metadata) != len(state.get("results", []))
                or any(item is not None and (
                    not isinstance(item, dict) or set(item) != {"provider", "model", "elapsed"}
                    or not isinstance(item["provider"], str) or not isinstance(item["model"], str)
                    or type(item["elapsed"]) not in (int, float) or not 0 <= item["elapsed"] < float("inf"))
                    for item in metadata)):
            raise ValueError("복원할 모델 정보가 올바르지 않습니다.")
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

        self._reset_copy()
        self.original = state["original"]
        self._clarifications = state.get("clarifications", "")
        self.revisions = list(state["revisions"])
        self.results = list(state["results"])
        self._result_revision_counts = list(counts)
        # Older snapshots do not record per-result clarification history.
        self._result_review_inputs = [None] * len(self.results)
        self.result_choice.blockSignals(True)
        try:
            self.result_choice.clear()
            self.result_choice.addItems(state["result_labels"])
            for index, item in enumerate(metadata or []):
                self.result_choice.setItemData(index, item)
            self.result_choice.setCurrentIndex(shown)
        finally:
            self.result_choice.blockSignals(False)
        self._shown_result = shown
        self._refresh_metadata()
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
        self._consent_epoch += 1
        self.send.setEnabled(not self._busy and not self._sleep_paused)
        self.retry_other.setEnabled(not self._busy and not self._sleep_paused)

    def _confirm(self, title, text, callback):
        dialog = QMessageBox(QMessageBox.Icon.Question, title, text,
                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        dialog.setEscapeButton(QMessageBox.StandardButton.No)
        self._confirmation = dialog
        dialog.finished.connect(lambda value: callback() if value == QMessageBox.StandardButton.Yes else None)
        dialog.open()
        return dialog

    def submit(self):
        if self._busy or self._sleep_paused:
            return
        text = self.input.toPlainText()
        if not text.strip():
            self.status.setText("먼저 요청 또는 추가 조건을 입력해 주세요.")
            self._show_notice("먼저 요청 또는 추가 조건을 입력해 주세요.")
            return
        provider = self.provider.currentData()
        try:
            profile = self._resolve_profile(provider)
        except ValueError as exc:
            self._show_notice(str(exc))
            return
        task, detail = self.task.currentData(), self.detail.currentData()
        original = self.original or text
        previous = self.result.toPlainText() if self.original else ""
        old_context = "\n\n".join(filter(None, [self._clarifications, *self.revisions]))
        revision = text if self.original else ""
        next_context = "\n\n".join(filter(None, [old_context, revision]))
        count = (self._result_revision_counts[self._shown_result]
                 if 0 <= self._shown_result < len(self._result_revision_counts) else 0)
        pending_revisions = [*self.revisions[count:], revision] if revision else []
        if provider != "offline":
            service = self._destination(provider)
            info = (f"원래 요청, 추가 조건과 이전 결과를 {service}에 전송합니다. "
                    "제공자는 내용을 저장하거나 서비스 개선에 사용할 수 있습니다. "
                    "학생·학부모·직원 식별정보, 민원 원문과 비공개 문서는 제외하거나 "
                    "가상 내용으로 바꾸고 기관의 사용 정책과 허용 서비스를 확인해 주세요.\n\n전송하시겠어요?")
            if provider == "jarvisclaw":
                info = (f"원래 요청, 추가 조건과 이전 결과를 {service}에 전송합니다.\n\n"
                        + _JARVIS_NOTICE + "\n\n전송하시겠어요?")
            if profile is not None and profile.provider == "ollama":
                info = f"{service} 로컬 서버에서 원래 요청, 추가 조건과 이전 결과를 처리합니다. 로컬 처리를 시작할까요?"
            expected = self._request_snapshot()
            dialog = self._confirm("로컬 AI 처리 확인" if profile is not None and profile.provider == "ollama" else "외부 AI 전송 확인", info,
                          lambda: self._submit_if_unchanged(expected, text, original, revision, provider, old_context, next_context, previous, task, detail, pending_revisions, profile))
            if isinstance(dialog, QMessageBox):
                dialog.setTextFormat(Qt.TextFormat.PlainText)
                dialog.setDetailedText(
                    f"서비스: {service}\n작업: {self.task.currentText()} ({task})\n"
                    f"상세 수준: {self.detail.currentText()} ({detail})\n\n"
                    f"원래 요청:\n{original}\n\n누적 조건:\n{old_context}\n\n"
                    f"이번 추가 조건:\n{revision}\n\n선택한 이전 결과 (편집 반영):\n{previous}\n\n"
                    "위 내용은 이번 요청의 입력값입니다. 앱 지시문과 모델/요청 메타데이터도 동반하며 전체 전송 원문은 아닙니다.")
                dialog.button(QMessageBox.StandardButton.Yes).setText(f"{service}로 전송")
                cancel = dialog.button(QMessageBox.StandardButton.No)
                cancel.setText("취소")
                cancel.setFocus()
                # The details live only in this confirmation, never in saved state.
                dialog.finished.connect(lambda _value: dialog.setDetailedText(""))
                dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            return
        self._submit_confirmed(text, original, revision, provider, old_context, next_context, previous, task, detail, pending_revisions)

    def _request_snapshot(self):
        return (self._draft_snapshot(), self.provider.currentData(), self.task.currentData(),
                self.detail.currentData(), self._clarifications, self._consent_epoch,
                self._profile_snapshot())

    def _submit_if_unchanged(self, expected, *args):
        if self._request_snapshot() != expected:
            self.status.setText("확인 중 입력이나 설정이 바뀌어 전송하지 않았어요. 내용을 다시 확인해 주세요.")
            self._show_notice("입력이나 설정이 바뀌어 전송하지 않았어요. 다시 확인해 주세요.")
            return
        self._submit_confirmed(*args)

    def _submit_confirmed(self, text, original, revision, provider, old_context, next_context, previous, task, detail, pending_revisions, profile=None):
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
        self._pending_review_input = (original, next_context)
        self.inline_notice.hide()
        self._hide_other_ai()
        self._set_busy(True)
        if provider == "offline":
            self.status.setText("기본 정리 중…")
        else:
            self.status.setText(f"{self.provider.currentText()} 응답을 기다리는 중입니다. "
                                "서비스에 따라 1분 이상 걸릴 수 있어요. "
                                "시간이 초과되면 입력과 이전 결과를 보존합니다.")

        def work():
            try:
                started = time.monotonic()
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
                    kwargs = dict(context=old_context, provider=profile.provider if profile else provider,
                                  revision=revision, previous_result=previous, task=task, detail=detail)
                    if profile is not None:
                        kwargs["profile"] = profile
                    reply = optimize_ai(original, **kwargs)
                reply = dict(reply)
                reply["_metadata"] = {
                    "provider": profile.provider if profile else provider,
                    "model": profile.model if profile else {"offline": "local", "vireonix": "auto",
                                                           "pollinations-classic": "openai", "jarvisclaw": "auto/free"}[provider],
                    "elapsed": max(0.0, time.monotonic() - started)}
                try:
                    self._signals.completed.emit(reply)
                except RuntimeError:  # application closed while request was in flight
                    pass
            except Exception as exc:
                try:
                    self._signals.failed.emit(_safe_generation_error(exc))
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
        comparison_second = self._comparison_active and self._comparison_index > 0
        self._save_result()
        # The result editor is usable before the first generation. Keep an
        # unslotted draft as a selectable entry instead of overwriting it.
        # It is deliberately not included in a first external AI request.
        unslotted_draft = self._shown_result < 0 and self.result.toPlainText() != ""
        if unslotted_draft:
            self.results.append(self.result.toPlainText())
            self._result_revision_counts.append(0)
            self._result_review_inputs.append(None)
            self.result_choice.addItem("직접 작성한 결과")
        self.original = original
        self._update_input_example()
        if revision:
            self.revisions.append(revision)
        if not comparison_second:
            cursor = self.conversation.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText("\n\n나: " + text + "\n션: " + ("AI로 정리했어요. 아래에서 살펴보고 수정해 주세요." if provider != "offline" else "입력과 조건을 기본 구조로 정리했어요."))
            self.conversation.setTextCursor(cursor)
        # A modeless template picker can still insert text while the input is
        # disabled. Only clear the exact draft that was submitted; if it has
        # changed in the meantime, preserve it for the next request.
        input_changed = not comparison_second and self.input.toPlainText() != text
        if not input_changed and not comparison_second:
            self.input.clear()
        self.results.append(reply["result"])
        self._result_revision_counts.append(len(self.revisions))
        self._result_review_inputs.append(self._pending_review_input if provider != "offline" else None)
        self.result_choice.addItem(f"{len(self.results)}번째 결과")
        self.result_choice.setItemData(len(self.results) - 1, reply.get("_metadata"))
        self.result_choice.setCurrentIndex(len(self.results) - 1)
        # A first item can already be selected by addItem before setCurrentIndex.
        if self._shown_result != len(self.results) - 1:
            self._select_result(len(self.results) - 1)
        self._refresh_metadata()
        notes = list(reply.get("notes", []))
        if provider != "offline":
            notes.extend(self._selected_review())
        if input_changed:
            notes.append("정리 중 바뀐 입력은 그대로 남겨 두었어요. 다음 요청에 사용할 내용을 확인해 주세요.")
        if unslotted_draft:
            notes.append("미리 작성한 결과는 ‘직접 작성한 결과’에서 확인할 수 있어요.")
        self.status.setText("\n".join(notes) if notes else "원래 의도와 조건을 확인해 주세요.")
        if not self._comparison_active:
            self._set_busy(False)
        self.input.setFocus()
        self.result_ready.emit()
        self.task_feedback.emit("success")

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
        self.task_feedback.emit("failure")
