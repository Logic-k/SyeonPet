"""Guided modeless dialog for the diagnose -> ask-back -> result workflow."""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from optimizer_core import utf16_length
from syeonpet.chat.dialogs import _ActionRow, sync_topmost_parent
from syeonpet.privacy.detector import mask_pii, summarize_pii
from .assembler import (
    _TONE_CHIPS,
    alternative_types,
    build_prompt,
    render_prompt_html,
    tuner_axes,
)
from .preferences import (
    EXTERNAL_AI,
    PreferenceStore,
    copy_to_clipboard,
    open_external,
)
from .rules import (
    _CIRCLED,
    _GENERIC,
    _MAX_INPUT,
    _MAX_QUESTIONS,
    _TYPE_BY_ID,
    _TYPES,
    CONFIRM_NEEDED,
    _ai_confirm_title,
    _clip_utf16,
    _completion_text,
    _facade_attr,
    _is_local,
    _service_label,
    _topic,
    detect_segments,
    diagnose,
    is_meaningless,
)
from .widgets import (
    INPUT_STARTERS,
    ProfileCardDialog,
    PromptQualityBar,
    REFINE_PRESETS,
    SlotFillBar,
    build_chip_button,
)

_FLOW_STYLES = """
    QDialog#flowDialog { background: #f3f6fa; color: #172b3a; }
    QDialog#flowDialog QLabel { color: #172b3a; }
    QLabel#flowTitle { font-size: 12pt; font-weight: 600; }
    QLabel#flowSection { font-weight: 600; }
    QLabel#flowMuted { color: #4a6375; }
    QLabel#flowNotice { background: #eef4d9; color: #46520f; border: 1px solid #b9c88a;
        border-radius: 4px; padding: 5px 7px; }
    QLabel#flowInlineError { background: #fff1ed; color: #722c1d;
        border: 1px solid #c38170; border-radius: 4px; padding: 4px 6px; }
    QLabel#flowWarningBanner { background: #fff3cd; color: #7a4f01;
        border: 1px solid #e6c675; border-radius: 4px; padding: 4px 6px; }
    QLabel#flowSuccessBanner { background: #e8f4ec; color: #1f5130;
        border: 1px solid #a8d4b7; border-radius: 4px; padding: 4px 6px; }
    QFrame#questionCard { background: #ffffff; border: 1px solid #c4d3dd; border-radius: 6px; }
    QFrame#tunerPanel, QFrame#flowSubPanel { background: #eaf1f6; border: 1px solid #c4d3dd; border-radius: 6px; }
    QPushButton#flowChip { background: #f8fbfd; color: #195b83; border: 1px solid #b8cedd;
        border-radius: 11px; padding: 3px 9px; font-size: 9pt; }
    QPushButton#flowChip:hover { background: #e4edf3; border-color: #195b83; }
    QPlainTextEdit, QTextEdit, QLineEdit, QTextBrowser {
        background: #ffffff; color: #172b3a; border: 1px solid #9aaebc; border-radius: 4px;
        padding: 4px; selection-background-color: #195b83; selection-color: #ffffff; }
    QPushButton { background: #ffffff; color: #172b3a; border: 1px solid #8fa5b4;
        border-radius: 4px; padding: 5px 9px; }
    QPushButton:hover { background: #e4edf3; }
    QPushButton:focus { border: 2px solid #195b83; }
    QPushButton:checked { background: #d9eaf4; border-color: #195b83; }
    QPushButton#flowPrimary { background: #195b83; color: #ffffff; border-color: #195b83;
        font-weight: 600; }
    QPushButton#flowPrimary:hover { background: #124969; }
    QPushButton#flowPrimary:disabled { background: #d5e0e6; color: #425a6a; }
    QPushButton:disabled { background: #e9eef2; color: #526a79; border-color: #afbec8; }
    QScrollBar:vertical { background: #dce5eb; width: 11px; margin: 0; }
    QScrollBar::handle:vertical { background: #6a8394; min-height: 20px; border-radius: 5px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


class _QuestionCard(QFrame):
    """Numbered card: title + reason + example chips + free-text answer."""

    def __init__(self, index: int, question: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("questionCard")
        self.question = question
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)
        title = f"{index}. {question['title']}" + ("  (없으면 비워두세요)" if question.get("optional") else "")
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-weight:600;color:#172b3a;")
        layout.addWidget(self.title_label)
        self.reason_label = QLabel("묻는 이유: " + question["reason"])
        self.reason_label.setStyleSheet("color:#4a6375;")
        self.reason_label.setWordWrap(True)
        layout.addWidget(self.reason_label)
        self.answer = QPlainTextEdit()
        self.answer.setAccessibleName(f"{index}번 질문 답변")
        self.answer.setPlaceholderText("아래 칩을 누르거나 직접 적어 주세요.")
        self.answer.setTabChangesFocus(True)
        self.answer.setMaximumHeight(52)
        layout.addWidget(self.answer)
        if question["chips"]:
            row = QHBoxLayout()
            row.setSpacing(4)
            hint = QLabel("예시 칩 — 누르면 답에 들어가요")
            hint.setStyleSheet("color:#4a6375;font-size:9pt;")
            row.addWidget(hint)
            row.addStretch()
            layout.addLayout(row)
            from syeonpet.chat.dialogs import _ActionRow
            chips_row = _ActionRow()
            chips_row.setSpacing(4)
            self.chip_buttons = []
            for chip in question["chips"]:
                button = QPushButton(chip)
                button.setAccessibleName(f"예시: {chip}")
                button.clicked.connect(lambda _checked=False, text=chip: self._fill(text))
                self.chip_buttons.append(button)
                chips_row.addWidget(button)
            chips_row.addStretch()
            layout.addLayout(chips_row)

    def _fill(self, text):
        current = self.answer.toPlainText().strip()
        if not current:
            self.answer.setPlainText(text)
        elif text not in current:
            self.answer.setPlainText(current + ", " + text)
        self.answer.setFocus()

    def value(self) -> str:
        return self.answer.toPlainText().strip()

    def set_value(self, text: str):
        self.answer.setPlainText(text)


class _ReplySignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class FlowDialog(QDialog):
    """The guided workflow as a modeless dialog hosted by PromptConversation.

    Parameters
    ----------
    parent : QWidget
    initial_text : seed request text (usually the panel's current input).
    provider_getter : callable returning (provider_id, profile|None); used for
        optional AI paths. Returning ('offline', None) disables them.
    on_finished : callable(text, type_label, original) invoked once per accepted
        completed prompt (also fired on multiple composite segments).
    """

    PAGE_INPUT, PAGE_WORK, PAGE_GENERATING, PAGE_RESULT = range(4)

    def __init__(self, parent=None, initial_text="", provider_getter=None, on_finished=None):
        super().__init__(parent)
        self.setObjectName("flowDialog")
        self.setWindowTitle("단계별 완성형 도우미")
        self.setAccessibleName("진단·되묻기·완성형 단계 도우미")
        self.setStyleSheet(_FLOW_STYLES)
        self.resize(560, 640)
        self._provider_getter = provider_getter or (lambda: ("offline", None))
        self._on_finished = on_finished
        self._segments = []
        self._segment_index = 0
        self._dropped_segments = 0
        self._busy_ai = False
        self._ai_kind = None
        # (segment index, flow generation) the in-flight AI call belongs to.
        # The generation bumps on every context change (new segment, restart,
        # close) so a reply landing later under another context is stale.
        self._ai_context = None
        self._flow_generation = 0
        self._signals = _ReplySignals(self)
        self._signals.done.connect(self._ai_done)
        self._signals.failed.connect(self._ai_failed)
        self._last_prompt = ""
        self._last_original = ""
        self._full_input_text = ""
        self._diag = {}
        self._cards = []
        self._answers = {}
        self._tuner_widgets = []
        self.tuner_free = None
        # Completed per-step results, keyed by segment index (memory only —
        # the record dies with the dialog and is never auto-saved to the
        # library). ``saved_text`` mirrors the text at the last explicit save.
        self._step_results = {}
        self._result_view_index = None
        self._back_target = None
        self._hide_notice_shown = False

        self._result_edit_timer = QTimer(self)
        self._result_edit_timer.setSingleShot(True)
        self._result_edit_timer.setInterval(250)
        self._result_edit_timer.timeout.connect(self._on_result_edit_settled)

        self.pages = QStackedWidget(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.addWidget(self.pages)
        self._build_input_page()
        self._build_work_page()
        self._build_generating_page()
        self._build_result_page()
        self.input_edit.setPlainText(initial_text)
        self.input_edit.textChanged.connect(self._refresh_start)
        self._refresh_start()
        self.pages.setCurrentIndex(self.PAGE_INPUT)
        sync_topmost_parent(self, parent)

    # ----- page builders ---------------------------------------------------

    def _build_input_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        title = QLabel("원하는 일을 한 줄로 적어 주세요")
        title.setObjectName("flowTitle")
        layout.addWidget(title)
        hint = QLabel("예: 이번 주에 한 일을 정리해서 과장님께 낼 부서 주간보고 써줘\n"
                      "입력만으로 빠진 조건을 진단하고, 답을 받아 완성형 프롬프트를 만들어요. "
                      "진단·생성은 외부 전송 없이 이 기기에서 처리해요 (Ctrl+Enter: 바로 시작).")
        hint.setObjectName("flowMuted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        starter_row = _ActionRow()
        self.starter_buttons = {}
        for chip_label, seed_text in INPUT_STARTERS:
            btn = build_chip_button(chip_label)
            btn.setAccessibleName("예시 요청 넣기: " + chip_label)
            btn.clicked.connect(lambda _c=False, t=seed_text: self._apply_input_starter(t))
            self.starter_buttons[chip_label] = btn
            starter_row.addWidget(btn)
        starter_row.addStretch()
        layout.addLayout(starter_row)

        tools_row = QHBoxLayout()
        self.load_file_btn = QPushButton("📂 파일 불러오기")
        self.load_file_btn.setToolTip("한글(.hwpx)·워드·엑셀·PDF·텍스트 문서 본문을 가져와 요청으로 씁니다.")
        self.load_file_btn.clicked.connect(self._import_file)
        tools_row.addWidget(self.load_file_btn)
        self.paste_btn = QPushButton("📋 붙여넣기")
        self.paste_btn.clicked.connect(self._paste_into_input)
        tools_row.addWidget(self.paste_btn)
        self.mask_input_pii_btn = QPushButton("🛡️ 개인정보 가리기")
        self.mask_input_pii_btn.clicked.connect(self._mask_input_pii)
        self.mask_input_pii_btn.hide()
        tools_row.addWidget(self.mask_input_pii_btn)
        tools_row.addStretch()
        self.input_char_count = QLabel("0 / 30,000자")
        self.input_char_count.setObjectName("flowMuted")
        tools_row.addWidget(self.input_char_count)
        layout.addLayout(tools_row)

        self.input_pii_banner = QLabel()
        self.input_pii_banner.setObjectName("flowWarningBanner")
        self.input_pii_banner.setWordWrap(True)
        self.input_pii_banner.hide()
        layout.addWidget(self.input_pii_banner)

        self.input_edit = QPlainTextEdit()
        self.input_edit.setAccessibleName("한 줄 요청 입력")
        self.input_edit.setPlaceholderText("할 일을 한 줄로 적어 주세요. (Ctrl+Enter로 진단 시작)")
        self.input_edit.setTabChangesFocus(True)
        self.input_edit.installEventFilter(self)
        layout.addWidget(self.input_edit, 1)
        row = QHBoxLayout()
        row.addStretch()
        self.start_button = QPushButton("진단 시작")
        self.start_button.setObjectName("flowPrimary")
        self.start_button.setAccessibleName("한 줄 요청 진단 시작")
        self.start_button.clicked.connect(self._start)
        row.addWidget(self.start_button)
        layout.addLayout(row)
        self.pages.addWidget(page)

    def _build_work_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.work_content = QWidget()
        self.work_layout = QVBoxLayout(self.work_content)
        self.work_layout.setContentsMargins(4, 4, 4, 4)
        self.work_layout.setSpacing(6)
        scroll.setWidget(self.work_content)
        outer.addWidget(scroll, 1)

        # Composite notice (hidden unless a multi-task request is detected).
        self.composite_notice = QLabel()
        self.composite_notice.setObjectName("flowNotice")
        self.composite_notice.setWordWrap(True)
        self.composite_notice.hide()
        self.work_layout.addWidget(self.composite_notice)

        diag_row = QHBoxLayout()
        self.verdict_label = QLabel()
        self.verdict_label.setObjectName("flowSection")
        self.verdict_label.setWordWrap(True)
        diag_row.addWidget(self.verdict_label, 1)
        self.percent_label = QLabel()
        self.percent_label.setObjectName("flowTitle")
        diag_row.addWidget(self.percent_label)
        self.work_layout.addLayout(diag_row)
        top_row = QHBoxLayout()
        self.step_label = QLabel()
        self.step_label.setObjectName("flowMuted")
        top_row.addWidget(self.step_label, 1)
        self.type_combo = QComboBox()
        self.type_combo.setAccessibleName("문서 유형 수동 선택")
        self.type_combo.setToolTip("자동 분류된 유형이 다르면 여기서 직접 바꿀 수 있어요.")
        for spec in (*_TYPES, _GENERIC):
            self.type_combo.addItem(spec["label"], spec["id"])
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        top_row.addWidget(self.type_combo)
        self.edit_input_button = QPushButton("✏️ 원문 수정")
        self.edit_input_button.setToolTip("적어 둔 요청 문장을 지우지 않고 입력 화면으로 돌아갑니다.")
        self.edit_input_button.clicked.connect(self._edit_original)
        top_row.addWidget(self.edit_input_button)
        self.restart_button = QPushButton("다른 요청 입력하기")
        self.restart_button.setAccessibleName("새 요청으로 돌아가기")
        self.restart_button.clicked.connect(self._restart)
        top_row.addWidget(self.restart_button)
        self.work_layout.addLayout(top_row)

        self.recognized_label = QLabel()
        self.recognized_label.setObjectName("flowSuccessBanner")
        self.recognized_label.setWordWrap(True)
        self.recognized_label.hide()
        self.work_layout.addWidget(self.recognized_label)

        # Unsaved-finished-results notice + finished-step access (composite
        # recall) — both stay hidden unless a finished result exists.
        self.unsaved_notice_work = QLabel()
        self.unsaved_notice_work.setObjectName("flowNotice")
        self.unsaved_notice_work.setWordWrap(True)
        self.unsaved_notice_work.hide()
        self.work_layout.addWidget(self.unsaved_notice_work)

        self.done_row_work = QWidget()
        self.done_row_work_layout = QHBoxLayout(self.done_row_work)
        self.done_row_work_layout.setContentsMargins(0, 0, 0, 0)
        self.done_row_work_layout.setSpacing(4)
        done_label = QLabel("완료된 작업:")
        done_label.setObjectName("flowMuted")
        self.done_row_work_layout.addWidget(done_label)
        self.done_row_work_layout.addStretch()
        self.done_row_work.hide()
        self.work_layout.addWidget(self.done_row_work)

        self.profile_row = QWidget()
        self.profile_row_layout = _ActionRow()
        self.profile_row.setLayout(self.profile_row_layout)
        self.profile_row_layout.setContentsMargins(0, 0, 0, 0)
        self.profile_row_layout.setSpacing(4)
        self.work_layout.addWidget(self.profile_row)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.work_layout.addWidget(self.summary_label)
        self.counter_label = QLabel()
        self.counter_label.setObjectName("flowMuted")
        self.work_layout.addWidget(self.counter_label)

        self.questions_holder = QWidget()
        self.questions_layout = QVBoxLayout(self.questions_holder)
        self.questions_layout.setContentsMargins(0, 0, 0, 0)
        self.questions_layout.setSpacing(6)
        self.work_layout.addWidget(self.questions_holder)

        ai_row = QHBoxLayout()
        self.ai_question_button = QPushButton("AI에게 보완 질문 추천받기 (선택)")
        self.ai_question_button.setAccessibleName("AI 보완 질문 추천")
        self.ai_question_button.setToolTip("선택한 외부 AI가 이 요청에서 더 물어볼 조건을 제안합니다. 전송을 확인한 뒤에만 요청이 나갑니다.")
        self.ai_question_button.clicked.connect(self._ask_ai_questions)
        ai_row.addWidget(self.ai_question_button)
        ai_row.addStretch()
        self.work_layout.addLayout(ai_row)
        self.ai_question_status = QLabel()
        self.ai_question_status.setObjectName("flowMuted")
        self.ai_question_status.setWordWrap(True)
        self.ai_question_status.hide()
        self.work_layout.addWidget(self.ai_question_status)

        # Pre-generation tuner (S2b): loading text first, axes appear after.
        self.tuner_panel = QFrame()
        self.tuner_panel.setObjectName("tunerPanel")
        tuner_layout = QVBoxLayout(self.tuner_panel)
        tuner_layout.setContentsMargins(8, 6, 8, 6)
        tuner_layout.setSpacing(4)
        self.tuner_loading = QLabel("표현·형식 옵션을 준비하는 중…")
        self.tuner_loading.setObjectName("flowMuted")
        tuner_layout.addWidget(self.tuner_loading)
        self.tuner_content = QWidget()
        self.tuner_axes_layout = QVBoxLayout(self.tuner_content)
        self.tuner_axes_layout.setContentsMargins(0, 0, 0, 0)
        self.tuner_axes_layout.setSpacing(6)
        self.tuner_content.hide()
        tuner_layout.addWidget(self.tuner_content)
        self.work_layout.addWidget(self.tuner_panel)

        bottom = QHBoxLayout()
        self.pref_clear = QPushButton("기억한 답변 지우기")
        self.pref_clear.setAccessibleName("이 유형에서 기억한 답변 지우기")
        self.pref_clear.setToolTip("이 기기에만 저장된, 이 유형의 반복 답변을 지웁니다.")
        self.pref_clear.clicked.connect(self._clear_preferences)
        self.pref_clear.hide()
        bottom.addWidget(self.pref_clear)
        bottom.addStretch()
        self.generate_button = QPushButton("완성형 만들기")
        self.generate_button.setObjectName("flowPrimary")
        self.generate_button.clicked.connect(self._generate)
        bottom.addWidget(self.generate_button)
        self.work_layout.addLayout(bottom)
        self.work_note = QLabel("각 항목에 답해 주시면 다음으로 넘어가요. 답이 없는 항목은 [확인 필요] 표시만 남기고 완성형에 반영돼요.")
        self.work_note.setObjectName("flowMuted")
        self.work_note.setWordWrap(True)
        self.work_layout.addWidget(self.work_note)
        self.work_layout.addStretch()
        self.pages.addWidget(page)

    def _build_generating_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 24, 16, 16)
        layout.setSpacing(8)
        title = QLabel("완성형 프롬프트를 만드는 중…")
        title.setObjectName("flowTitle")
        layout.addWidget(title)
        self.stage_labels = []
        for stage in ("① 입력과 답변 모으는 중", "② 완성형 프롬프트 구성하는 중", "③ 마무리 점검 중"):
            label = QLabel(stage)
            label.setObjectName("flowMuted")
            layout.addWidget(label)
            self.stage_labels.append(label)
        layout.addStretch()
        self.pages.addWidget(page)

    def _build_result_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        head = QHBoxLayout()
        self.result_title = QLabel()
        self.result_title.setObjectName("flowTitle")
        head.addWidget(self.result_title, 1)
        self.back_button = QPushButton()
        self.back_button.setAccessibleName("이전 화면으로 돌아가기")
        self.back_button.clicked.connect(self._back)
        self.back_button.hide()
        head.addWidget(self.back_button)
        self.complete_label = QLabel("")  # filled by _render_record from real answers
        self.complete_label.setStyleSheet("font-weight:600;color:#2f6b3a;")
        head.addWidget(self.complete_label)
        layout.addLayout(head)
        self.result_type_label = QLabel()
        self.result_type_label.setObjectName("flowMuted")
        layout.addWidget(self.result_type_label)

        self.unsaved_notice_result = QLabel()
        self.unsaved_notice_result.setObjectName("flowNotice")
        self.unsaved_notice_result.setWordWrap(True)
        self.unsaved_notice_result.hide()
        layout.addWidget(self.unsaved_notice_result)

        self.done_row_result = QWidget()
        self.done_row_result_layout = QHBoxLayout(self.done_row_result)
        self.done_row_result_layout.setContentsMargins(0, 0, 0, 0)
        self.done_row_result_layout.setSpacing(4)
        done_label_result = QLabel("완료된 작업:")
        done_label_result.setObjectName("flowMuted")
        self.done_row_result_layout.addWidget(done_label_result)
        self.done_row_result_layout.addStretch()
        self.done_row_result.hide()
        layout.addWidget(self.done_row_result)

        self.result_edit = QTextEdit()
        self.result_edit.setAccessibleName("완성형 프롬프트 편집")
        self.result_edit.setMinimumHeight(150)
        self.result_edit.setTabChangesFocus(True)
        self.result_edit.textChanged.connect(lambda: self._result_edit_timer.start())
        layout.addWidget(self.result_edit, 2)
        self.render_view = QTextBrowser()
        self.render_view.setAccessibleName("완성형 프롬프트 문서 형태 표시")
        self.render_view.setOpenExternalLinks(False)
        self.render_view.setMinimumHeight(150)
        self.render_view.hide()
        layout.addWidget(self.render_view, 2)

        self.slot_fill_bar = SlotFillBar()
        self.slot_fill_bar.applied.connect(self._apply_slot_fills)
        layout.addWidget(self.slot_fill_bar)

        self.quality_bar = PromptQualityBar()
        self.quality_bar.supplement_requested.connect(self._append_missing_conditions)
        layout.addWidget(self.quality_bar)

        view_row = QHBoxLayout()
        self.view_toggle = QPushButton("문서 형태로 보기")
        self.view_toggle.setCheckable(True)
        self.view_toggle.toggled.connect(self._toggle_render)
        view_row.addWidget(self.view_toggle)
        view_row.addStretch()
        self.copy_button = QPushButton("프롬프트 복사")
        self.copy_button.clicked.connect(self._copy)
        self.save_button = QPushButton("보관함에 저장")
        self.save_button.clicked.connect(self._save)
        self.new_button = QPushButton("새 요청으로 돌아가기")
        self.new_button.clicked.connect(self._restart)
        view_row.addWidget(self.new_button)
        view_row.addWidget(self.save_button)
        view_row.addWidget(self.copy_button)
        layout.addLayout(view_row)

        sub_view_row = _ActionRow()
        self.apply_to_chat_button = QPushButton("💬 대화창에 바로 넣기")
        self.apply_to_chat_button.setToolTip("보관함에 저장하지 않고 대화창 결과 목록으로 바로 보냅니다.")
        self.apply_to_chat_button.clicked.connect(self._apply_to_chat)
        self.apply_to_chat_button.setVisible(self._on_finished is not None)
        sub_view_row.addWidget(self.apply_to_chat_button)
        self.export_doc_button = QPushButton("💾 문서로 저장")
        self.export_doc_button.setToolTip("완성된 프롬프트를 한글(.hwpx)·텍스트·마크다운 파일로 저장합니다.")
        self.export_doc_button.clicked.connect(self._export_document)
        sub_view_row.addWidget(self.export_doc_button)
        self.mask_result_pii_button = QPushButton("🛡️ 개인정보 가리기")
        self.mask_result_pii_button.clicked.connect(self._mask_result_pii)
        self.mask_result_pii_button.hide()
        sub_view_row.addWidget(self.mask_result_pii_button)
        sub_view_row.addStretch()
        layout.addLayout(sub_view_row)

        self.alt_type_row = QWidget()
        self.alt_type_layout = _ActionRow()
        self.alt_type_row.setLayout(self.alt_type_layout)
        self.alt_type_layout.setContentsMargins(0, 0, 0, 0)
        self.alt_type_layout.setSpacing(4)
        layout.addWidget(self.alt_type_row)

        refine_title = QLabel("다듬기")
        refine_title.setObjectName("flowSection")
        layout.addWidget(refine_title)
        refine_preset_row = _ActionRow()
        self.refine_preset_buttons = {}
        for label, instruction in REFINE_PRESETS:
            btn = build_chip_button(label)
            btn.clicked.connect(lambda _c=False, inst=instruction: self._apply_refine_preset(inst))
            self.refine_preset_buttons[label] = btn
            refine_preset_row.addWidget(btn)
        refine_preset_row.addStretch()
        layout.addLayout(refine_preset_row)

        refine_row = QHBoxLayout()
        self.refine_input = QLineEdit()
        self.refine_input.setAccessibleName("다듬기 한 줄 지시")
        self.refine_input.setPlaceholderText("바꿀 방향을 한 줄로 남겨 주세요 (예: 더 짧게)")
        self.refine_input.setMaxLength(2000)
        self.refine_button = QPushButton("다시 만들기")
        self.refine_button.setToolTip("AI 정리로 다듬습니다. AI 방식이 선택돼 있고 전송을 확인해야만 요청이 나갑니다.")
        self.refine_button.clicked.connect(self._refine)
        self.refine_cancel = QPushButton("취소")
        self.refine_cancel.clicked.connect(lambda: self.refine_input.clear())
        refine_row.addWidget(self.refine_input, 1)
        refine_row.addWidget(self.refine_button)
        refine_row.addWidget(self.refine_cancel)
        layout.addLayout(refine_row)

        handoff = QLabel("외부 AI로 옮기기 — 누르면 프롬프트를 복사한 뒤 사이트를 열어요")
        handoff.setObjectName("flowSection")
        handoff.setWordWrap(True)
        layout.addWidget(handoff)
        self.external_buttons = {}
        ext_row = _ActionRow()
        for key, name, _url in EXTERNAL_AI:
            button = QPushButton(name)
            button.setToolTip("프롬프트를 복사하고 " + name + "을(를) 엽니다. 자동으로 넣어 주지는 않으니 열린 화면에 붙여 넣어 주세요.")
            button.setAccessibleName(name + " 열기 (복사한 프롬프트를 붙여 넣기)")
            button.clicked.connect(lambda _c=False, k=key: self._open_external(k))
            self.external_buttons[key] = button
            ext_row.addWidget(button)
        ext_row.addStretch()
        layout.addLayout(ext_row)

        self.next_step_button = QPushButton()
        self.next_step_button.setObjectName("flowPrimary")
        self.next_step_button.clicked.connect(self._next_segment)
        self.next_step_button.hide()
        layout.addWidget(self.next_step_button)
        self.result_status = QLabel()
        self.result_status.setObjectName("flowMuted")
        self.result_status.setWordWrap(True)
        layout.addWidget(self.result_status)
        layout.addStretch()
        self.pages.addWidget(page)

    # ----- flow control ----------------------------------------------------

    def _defer(self, ms, fn):
        """QTimer.singleShot that survives the dialog being closed early."""
        def run():
            try:
                fn()
            except RuntimeError:
                pass
        QTimer.singleShot(ms, run)

    def eventFilter(self, obj, event):
        if obj is getattr(self, "input_edit", None) and event.type() == QEvent.Type.KeyPress:
            if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                if self.start_button.isEnabled():
                    self._start()
                return True
        return super().eventFilter(obj, event)

    def _apply_input_starter(self, text: str) -> None:
        self.input_edit.setPlainText(text)
        self.input_edit.setFocus()

    def _paste_into_input(self) -> None:
        try:
            clip = QApplication.clipboard().text()
        except Exception:
            clip = ""
        if clip:
            current = self.input_edit.toPlainText()
            self.input_edit.setPlainText((current.rstrip() + "\n\n" + clip).strip() if current.strip() else clip)
            self.input_edit.setFocus()

    def _import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "요청·자료 문서 불러오기",
            "",
            "문서 파일 (*.txt *.md *.hwpx *.docx *.xlsx *.csv *.pdf);;모든 파일 (*.*)",
        )
        if not path:
            return
        try:
            from syeonpet.finder.extractors import extract_document_text
            text, badge = extract_document_text(path)
        except Exception as exc:
            box = QMessageBox(QMessageBox.Icon.Warning, "파일 열기 실패", f"파일을 읽지 못했습니다:\n{exc}",
                              QMessageBox.StandardButton.Ok, self)
            sync_topmost_parent(box, self)
            box.open()
            return
        clean = (text or "").strip()
        if not clean:
            box = QMessageBox(QMessageBox.Icon.Information, "본문 읽기 불가",
                              badge or "이 파일에서는 텍스트를 추출할 수 없어요.",
                              QMessageBox.StandardButton.Ok, self)
            sync_topmost_parent(box, self)
            box.open()
            return
        seeded = f"아래 문서({Path(path).name}) 내용을 바탕으로 업무 초안을 작성해 주세요:\n\n{clean}"
        if utf16_length(seeded) > _MAX_INPUT:
            seeded = _clip_utf16(seeded, _MAX_INPUT)
        self.input_edit.setPlainText(seeded)
        self.input_edit.setFocus()

    def _mask_input_pii(self) -> None:
        raw = self.input_edit.toPlainText()
        if not raw.strip():
            return
        self.input_edit.setPlainText(mask_pii(raw))

    def _refresh_start(self):
        raw = self.input_edit.toPlainText()
        u_len = utf16_length(raw)
        self.input_char_count.setText(f"{u_len:,} / 30,000자")
        self.start_button.setEnabled(bool(raw.strip()) and u_len <= _MAX_INPUT)
        summary = summarize_pii(raw) if raw.strip() else ""
        if summary:
            self.input_pii_banner.setText(
                f"⚠️ 개인정보({summary})가 감지되었어요. 외부 AI를 쓸 계획이라면 '🛡️ 개인정보 가리기'를 먼저 눌러 주세요."
            )
            self.input_pii_banner.show()
            self.mask_input_pii_btn.show()
        else:
            self.input_pii_banner.hide()
            self.mask_input_pii_btn.hide()

    def _start(self):
        text = self.input_edit.toPlainText().strip()
        if not text or utf16_length(text) > _MAX_INPUT:
            return
        self._full_input_text = text
        segments = detect_segments(text)
        self._segments = segments[:4]
        self._dropped_segments = len(segments) - len(self._segments)
        self._segment_index = 0
        # A new request owns a fresh result set — finished results of the
        # previous request were memory-only and are dropped here.
        self._step_results = {}
        self._result_view_index = None
        self._back_target = None
        self._hide_notice_shown = False
        self._begin_segment()

    def _begin_segment(self):
        # A new segment is a new context — any AI reply still in flight was
        # issued for the previous one and must be discarded when it lands.
        self._flow_generation += 1
        segment = self._segments[self._segment_index]
        text = segment["text"]
        type_id = segment["type"]
        self._last_original = text
        self._diag = diagnose(text, type_id)
        self._answers = {}
        self._result_view_index = self._segment_index
        self._back_target = None
        # New stage = new task: drop stale tuner controls and free text so a
        # previous segment's choices can never leak into this prompt.
        self._tuner_widgets = []
        self.tuner_free = None
        self._populate_work_page()
        self.pages.setCurrentIndex(self.PAGE_WORK)
        # Tuner loads asynchronously after the questions, like the original flow.
        self.tuner_loading.show()
        self.tuner_content.hide()
        self._defer(600, self._load_tuner)

    def _populate_work_page(self):
        diag = self._diag
        self.verdict_label.setText(diag["verdict"])
        self.percent_label.setText(f"완성도 {diag['percent']}%")
        total = len(self._segments)
        self.step_label.setText(f"{self._segment_index + 1}/{total} 단계 · '{diag['label']}' 유형"
                                if total > 1 else f"'{diag['label']}' 유형으로 진단")
        with QSignalBlocker(self.type_combo):
            idx = self.type_combo.findData(diag["type"])
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)
        recognized = diag.get("recognized") or []
        if recognized:
            self.recognized_label.setText("✓ 입력에서 이미 반영됨: " + " · ".join(recognized))
            self.recognized_label.show()
        else:
            self.recognized_label.hide()
        if total > 1:
            steps = " / ".join(f"{i + 1}. {_TYPE_BY_ID[s['type']]['label']}"
                               for i, s in enumerate(self._segments))
            note = ("여러 작업이 섞인 요청으로 보여요 — 하나씩 순서대로 진행해요.\n"
                    "한꺼번에 시키면 중간에 확인할 수 없어 결과가 어긋날 수 있어요.\n단계: " + steps)
            if self._dropped_segments:
                note += (f"\n나머지 작업 {self._dropped_segments}개는 이번 흐름에서 다루지 않아요 — "
                         "따로 다시 요청해 주세요.")
            self.composite_notice.setText(note)
            self.composite_notice.show()
        else:
            self.composite_notice.hide()
        titles = " · ".join(diag["missing_titles"]) or "없음"
        self.summary_label.setText(f"빠진 조건: {titles}\n"
                                   "아래 항목에 한 번에 답해 주시면 완성형을 만들 수 있어요.")

        while self.questions_layout.count():
            item = self.questions_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._cards = []
        prefs = {}
        try:
            prefs = PreferenceStore().prefilled(self._diag["type"])
        except OSError:
            pass
        self.pref_clear.setVisible(bool(prefs))
        for index, question in enumerate(diag["questions"], 1):
            card = _QuestionCard(index, question)
            if question["id"] in self._answers and self._answers[question["id"]]:
                card.set_value(self._answers[question["id"]])
            elif question["id"] in prefs:
                card.set_value(prefs[question["id"]])
                self._answers[question["id"]] = prefs[question["id"]]
            card.answer.textChanged.connect(self._sync_counter)
            self.questions_layout.addWidget(card)
            self._cards.append(card)
        self._refresh_profile_row()
        self._sync_counter()
        # AI supplement is optional and consent-gated; hidden without an AI path.
        provider, _profile = self._provider_getter()
        self.ai_question_button.setVisible(provider != "offline"
                                           and len(diag["questions"]) < _MAX_QUESTIONS)
        self.ai_question_status.hide()
        # Reached via "다음 작업 이어가기": finished results stay reachable and
        # any of them still unsaved is announced without blocking progress.
        self._update_done_row(None)
        self._update_unsaved_notice(None)

    def _carry_over_answers_to_diag(self, target_diag: dict) -> None:
        """Reuse semantically equivalent typed answers when switching task types."""
        groups = (
            ("week_results", "agenda", "status", "topic", "finding", "goal", "demand"),
            ("schedule", "schedule_plan", "next_week"),
            ("week_issues", "risk", "basis_facts", "reason"),
            ("help_needed", "request", "remedy", "stance"),
            ("sender_target", "audience", "info", "target_topic", "subject_grade"),
            ("content", "evidence", "objective_flow", "background"),
        )
        for q in target_diag.get("questions", ()):
            qid = q["id"]
            if self._answers.get(qid, "").strip():
                continue
            for grp in groups:
                if qid in grp:
                    donor = next((self._answers.get(other, "").strip()
                                  for other in grp if other != qid and self._answers.get(other, "").strip()), "")
                    if donor:
                        self._answers[qid] = donor
                    break

    def _on_type_changed(self) -> None:
        new_type = self.type_combo.currentData()
        if not new_type or not self._last_original or new_type == self._diag.get("type"):
            return
        self._flow_generation += 1
        self._sync_counter()
        if self._segments and 0 <= self._segment_index < len(self._segments):
            self._segments[self._segment_index]["type"] = new_type
        new_diag = diagnose(self._last_original, new_type)
        self._carry_over_answers_to_diag(new_diag)
        self._diag = new_diag
        self._populate_work_page()
        self.tuner_loading.show()
        self.tuner_content.hide()
        self._defer(120, self._load_tuner)

    def _edit_original(self) -> None:
        self._result_edit_timer.stop()
        self._flow_generation += 1
        self._sync_result_record()
        restore = self._full_input_text or self._last_original
        if restore:
            self.input_edit.setPlainText(restore)
        self.pages.setCurrentIndex(self.PAGE_INPUT)
        self.input_edit.setFocus()

    def _refresh_profile_row(self) -> None:
        while self.profile_row_layout.count():
            item = self.profile_row_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        lbl = QLabel("🪪 내 정보 카드:")
        lbl.setObjectName("flowMuted")
        self.profile_row_layout.addWidget(lbl)
        try:
            cards = PreferenceStore().load_profile_cards()
        except OSError:
            cards = []
        for card_data in cards:
            btn = build_chip_button(card_data["name"])
            btn.setToolTip("클릭하면 저장된 소속·대상·서명을 빈 항목에 바로 채워요.")
            btn.clicked.connect(lambda _c=False, cd=card_data: self._apply_profile_card(cd))
            self.profile_row_layout.addWidget(btn)
        manage_btn = build_chip_button("＋ 내 정보 등록·관리")
        manage_btn.clicked.connect(self._manage_profile_cards)
        self.profile_row_layout.addWidget(manage_btn)
        self.profile_row_layout.addStretch()

    def _manage_profile_cards(self) -> None:
        dlg = ProfileCardDialog(self, store=PreferenceStore())
        dlg.exec()
        self._refresh_profile_row()

    def _apply_profile_card(self, card_data: dict) -> None:
        org = card_data.get("org", "").strip()
        audience = card_data.get("audience", "").strip()
        sender = card_data.get("sender", "").strip()
        tone = card_data.get("tone", "").strip()
        extracted = self._diag.setdefault("extracted", {})
        if org and not extracted.get("organization"):
            extracted["organization"] = org
        if audience and not extracted.get("explicit_audience"):
            extracted["explicit_audience"] = audience
            extracted["audience"] = audience
        if sender and not extracted.get("sender"):
            extracted["sender"] = sender
        if tone and not extracted.get("tone"):
            extracted["tone"] = tone
        for card in self._cards:
            if card.value():
                continue
            qid = card.question["id"]
            if qid == "sender_target" and (org or audience):
                card.set_value(" ".join(filter(None, [org, audience])))
            elif qid == "audience" and audience:
                card.set_value(audience)
            elif qid in ("goal", "info", "context") and org:
                card.set_value(org)
        self._sync_counter()
        self.ai_question_status.setText(f"'{card_data['name']}' 정보를 빈 항목과 맥락에 반영했어요.")
        self.ai_question_status.show()

    def _sync_counter(self):
        for card in self._cards:
            self._answers[card.question["id"]] = card.value()
        answered = sum(1 for card in self._cards if card.value())
        self.counter_label.setText(f"답변 완성도 {answered}/{len(self._cards)}")

    def _load_tuner(self):
        if self.pages.currentIndex() != self.PAGE_WORK:
            return
        while self.tuner_axes_layout.count():
            item = self.tuner_axes_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._tuner_widgets = []
        intro = QLabel("바꾸고 싶은 표현이나 형식이 있다면 아래에서 선택해 보세요 — 아무것도 고르지 않아도 만들 수 있어요\n"
                       "방금 요청에 맞춰 준비한 옵션이에요. 켜 둔 항목은 모두 완성형에 반영돼요.")
        intro.setWordWrap(True)
        intro.setObjectName("flowMuted")
        self.tuner_axes_layout.addWidget(intro)
        meaningless = is_meaningless(self._last_original) and self._diag["type"] == "generic"
        for axis in tuner_axes(self._diag["type"], meaningless):
            holder = QWidget()
            layout = QVBoxLayout(holder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(2)
            title = QLabel(axis["title"])
            title.setStyleSheet("font-weight:600;")
            layout.addWidget(title)
            if axis["kind"] == "slider":
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(0, len(axis["steps"]) - 1)
                slider.setSingleStep(1)
                slider.setTickPosition(QSlider.TickPosition.TicksBelow)
                slider.setTickInterval(1)
                slider.setAccessibleName(axis["title"])
                value_label = QLabel("기본(미선택)")
                value_label.setObjectName("flowMuted")
                slider.valueChanged.connect(
                    lambda v, steps=axis["steps"], lab=value_label:
                        lab.setText(steps[v] if v else "기본(미선택)"))
                row = QHBoxLayout()
                row.addWidget(slider, 1)
                row.addWidget(value_label)
                layout.addLayout(row)
                self._tuner_widgets.append(("slider", axis["id"], slider, axis["steps"]))
            else:
                from syeonpet.chat.dialogs import _ActionRow
                row = _ActionRow()
                buttons = []
                for option in axis["options"]:
                    button = QPushButton(option)
                    button.setCheckable(True)
                    button.setAccessibleName(axis["title"] + ": " + option)
                    buttons.append(button)
                    row.addWidget(button)
                row.addStretch()
                layout.addLayout(row)
                self._tuner_widgets.append(("chips", axis["id"], buttons, axis["options"]))
            self.tuner_axes_layout.addWidget(holder)
        tone = QWidget()
        tone_layout = QVBoxLayout(tone)
        tone_layout.setContentsMargins(0, 0, 0, 0)
        tone_layout.setSpacing(2)
        tone_title = QLabel("말투 조절")
        tone_title.setStyleSheet("font-weight:600;")
        tone_layout.addWidget(tone_title)
        from syeonpet.chat.dialogs import _ActionRow
        tone_row = _ActionRow()
        tone_buttons = []
        for label in _TONE_CHIPS:
            button = QPushButton(label)
            button.setCheckable(True)
            tone_buttons.append(button)
            tone_row.addWidget(button)
        tone_row.addStretch()
        tone_layout.addLayout(tone_row)
        self._tuner_widgets.append(("tone_chips", "tone_chips", tone_buttons, _TONE_CHIPS))
        self.tuner_axes_layout.addWidget(tone)
        free_label = QLabel("직접 적는 표현·형식")
        free_label.setStyleSheet("font-weight:600;")
        self.tuner_free = QLineEdit()
        self.tuner_free.setAccessibleName("직접 적는 표현·형식")
        self.tuner_free.setPlaceholderText("원하는 표현 방식을 자유롭게 적어 주세요.")
        self.tuner_free.setMaxLength(500)
        free_holder = QWidget()
        free_layout = QVBoxLayout(free_holder)
        free_layout.setContentsMargins(0, 0, 0, 0)
        free_layout.addWidget(free_label)
        free_layout.addWidget(self.tuner_free)
        self.tuner_axes_layout.addWidget(free_holder)
        self.tuner_loading.hide()
        self.tuner_content.show()

    def _collect_tuner(self) -> dict:
        tuner = {"chips": [], "tone_chips": [], "free": "", "length_scale": None, "sliders": {}}
        for kind, axis_id, widget, options in self._tuner_widgets:
            if kind == "slider":
                value = options[widget.value()]
                if widget.value() > 0:  # first step means "unset" by default
                    tuner["sliders"][axis_id] = value
                    if axis_id == "length_scale":
                        tuner["length_scale"] = value
                    elif axis_id == "status_detail":
                        tuner["chips"].append("처리 상황 " + value)
            elif kind == "tone_chips":
                tuner["tone_chips"] = [options[i] for i, b in enumerate(widget) if b.isChecked()]
            else:
                tuner["chips"].extend(options[i] for i, b in enumerate(widget) if b.isChecked())
        free = getattr(self, "tuner_free", None)
        if free is not None and free.text().strip():
            tuner["free"] = free.text().strip()
        return tuner

    def _generate(self):
        if self._busy_ai:  # an AI call is in flight — never interleave prompts
            self.work_note.setText("AI 응답을 기다리는 중이에요. 끝나면 다시 눌러 주세요.")
            return
        self._sync_counter()
        self.pages.setCurrentIndex(self.PAGE_GENERATING)
        for label in self.stage_labels:
            label.setStyleSheet("color:#4a6375;")
        self._gen_stage = 0
        self._advance_stage()

    def _advance_stage(self):
        if self.pages.currentIndex() != self.PAGE_GENERATING:
            return
        if self._gen_stage < len(self.stage_labels):
            self.stage_labels[self._gen_stage].setStyleSheet("color:#172b3a;font-weight:600;")
            self._gen_stage += 1
            self._defer(320, self._advance_stage)
        else:
            self._finish_generation()

    def _finish_generation(self):
        prompt = build_prompt(self._diag["type"], self._last_original,
                              self._diag["questions"], self._answers,
                              self._diag["extracted"], self._collect_tuner(),
                              seeded=self._diag.get("seeded_topics"))
        self._show_result(prompt)
        self._record_preferences()

    def _record_preferences(self):
        try:
            store = PreferenceStore()
            for card in self._cards:
                store.record(self._diag["type"], card.question["id"], card.value())
        except OSError:
            pass

    def _record_current_result(self, prompt: str):
        """Keep this segment's finished text under its index (memory only)."""
        rec = self._step_results.setdefault(self._segment_index, {
            "type": self._diag["type"],
            "label": self._diag["label"],
            "original": self._last_original,
            "revisions": [f"{card.question['title']}: {card.value()}"
                          for card in self._cards if card.value()],
            "text": "",
            "saved_text": None,
        })
        rec["type"] = self._diag["type"]
        rec["label"] = self._diag["label"]
        rec["text"] = prompt
        rec["answered"] = sum(1 for card in self._cards if card.value())
        rec["total"] = len(self._cards)

    def _sync_result_record(self):
        """Carry the on-screen edits back into the record being viewed."""
        if self.pages.currentIndex() != self.PAGE_RESULT:
            return
        rec = self._step_results.get(self._result_view_index)
        if rec is not None:
            rec["text"] = self.result_edit.toPlainText()

    def _render_record(self, index: int):
        """Show one recorded result; the current segment stays fully editable."""
        rec = self._step_results[index]
        current = index == self._segment_index
        self._result_view_index = index
        with QSignalBlocker(self.result_edit):
            self.result_edit.setPlainText(rec["text"])
        self.render_view.setHtml(render_prompt_html(rec["text"]))
        if current:
            self.result_title.setText("완성형 프롬프트")
            self.result_type_label.setText(f"'{rec['label']}' 형식으로 만들었어요")
        else:
            self.result_title.setText(f"{index + 1}단계 완료 결과")
            self.result_type_label.setText(
                f"'{rec['label']}' 형식으로 만든 결과예요 — "
                "복사하거나 보관함에 저장할 수 있어요")
        answered = rec.get("answered", 0)
        total = rec.get("total", 0)
        self.complete_label.setText(_completion_text(answered, total))
        self.complete_label.setStyleSheet(
            "font-weight:600;color:#2f6b3a;" if total <= 0 or answered >= total
            else "font-weight:600;color:#8a6d1a;")
        provider, _profile = self._provider_getter()
        # AI 다듬기는 진행 중인 단계의 맥락만 쓰므로 완료 결과 열람 중에는 잠근다.
        self.refine_input.setEnabled(current)
        self.refine_button.setEnabled(current and provider != "offline" and not self._busy_ai)
        if current and provider == "offline":
            self.refine_button.setToolTip("다듬기 재생성은 AI 정리 방식이 필요해요. 대화창의 정리 방식에서 AI를 선택해 주세요.")
        remaining = len(self._segments) - self._segment_index - 1
        if current and remaining > 0:
            nxt = _TYPE_BY_ID[self._segments[self._segment_index + 1]["type"]]["label"]
            self.next_step_button.setText(f"다음 작업 이어가기 ({self._segment_index + 2}/{len(self._segments)}): {nxt}")
            self.next_step_button.show()
        else:
            self.next_step_button.hide()
        self.back_button.setVisible(not current)
        self._refresh_alt_types(rec.get("type", self._diag.get("type", "generic")), enabled=current)
        self._refresh_result_helpers()

    def _show_result(self, prompt: str, refined: bool = False):
        self._last_prompt = prompt
        self._record_current_result(prompt)
        self._render_record(self._segment_index)
        if refined:
            self.result_type_label.setText(
                f"'{self._diag['label']}' 형식으로 만들었어요 · 다듬은 결과")
        self.result_status.setText("이대로 바로 사용해도 돼요 — 직접 고치거나 내용을 더 넣으면 정확해져요.")
        self.pages.setCurrentIndex(self.PAGE_RESULT)
        self._update_done_row(self._segment_index)
        self._update_unsaved_notice(self._segment_index)

    # ----- finished-step recall (memory only; never auto-saved) -------------

    def _view_step(self, index: int):
        """Open a finished step's result for reading, copying and saving."""
        if index not in self._step_results:
            return
        from_work = self.pages.currentIndex() == self.PAGE_WORK
        self._sync_result_record()
        if index != self._segment_index:
            self._back_target = self.PAGE_WORK if from_work else None
            self.back_button.setText("질문 화면으로 돌아가기" if from_work
                                     else "이번 단계 결과로 돌아가기")
        self._render_record(index)
        self.result_status.setText(
            "완료된 작업 결과를 보고 있어요 — 복사·저장 후 돌아가기로 진행을 이어갈 수 있어요."
            if index != self._segment_index else
            "이대로 바로 사용해도 돼요 — 직접 고치거나 내용을 더 넣으면 정확해져요.")
        self.pages.setCurrentIndex(self.PAGE_RESULT)
        self._update_done_row(index)
        self._update_unsaved_notice(index)

    def _back(self):
        self._sync_result_record()
        if self._back_target == self.PAGE_WORK:
            self._result_view_index = self._segment_index
            self._back_target = None
            self.pages.setCurrentIndex(self.PAGE_WORK)
            self._update_done_row(None)
            self._update_unsaved_notice(None)
        else:
            self._back_target = None
            self._view_step(self._segment_index)

    def _fill_done_row(self, layout, viewing):
        """(Re)build the '완료된 작업' chips inside one row layout."""
        while layout.count() > 1:
            item = layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                # Unparent first so a pending-deletion chip can never be
                # found as a child of the row again.
                widget.setParent(None)
                widget.deleteLater()
        for index in sorted(self._step_results):
            rec = self._step_results[index]
            text = f"{_CIRCLED[index]} {rec['label']}"
            if rec.get("saved_text") == rec["text"]:
                text += " · 저장됨"
            button = QPushButton(text)
            button.setCheckable(True)
            button.setChecked(index == viewing)
            button.setAccessibleName(f"{index + 1}단계 결과 다시 열기")
            button.clicked.connect(lambda _checked=False, i=index: self._view_step(i))
            layout.addWidget(button)
        layout.addStretch()

    def _update_done_row(self, viewing):
        """Refresh both chip rows; ``viewing`` marks the chip being looked at."""
        self._fill_done_row(self.done_row_work_layout, viewing)
        self._fill_done_row(self.done_row_result_layout, viewing)
        self.done_row_work.setVisible(bool(self._step_results))
        self.done_row_result.setVisible(len(self._step_results) > 1)

    def _update_unsaved_notice(self, viewing):
        """Announce finished results that were never saved to the library.

        The work page counts every record (all are finished previous steps);
        the result page leaves out the record currently on screen.
        """
        def count(exclude):
            return sum(1 for i, rec in self._step_results.items()
                       if i != exclude and rec.get("saved_text") != rec["text"])
        work_unsaved = count(None)
        result_unsaved = count(viewing)

        def text(number):
            return (f"보관함에 저장하지 않은 완료 결과가 {number}건 있어요 — "
                    "'완료된 작업'에서 다시 열람·복사·저장할 수 있어요." if number else "")
        self.unsaved_notice_work.setText(text(work_unsaved))
        self.unsaved_notice_work.setVisible(bool(work_unsaved))
        self.unsaved_notice_result.setText(text(result_unsaved))
        self.unsaved_notice_result.setVisible(bool(result_unsaved))

    def _notice_before_hide(self):
        """Non-blocking heads-up when the window goes away with unsaved results."""
        self._sync_result_record()
        unsaved = sum(1 for rec in self._step_results.values()
                      if rec.get("saved_text") != rec["text"])
        if not unsaved or self._hide_notice_shown:
            return
        self._hide_notice_shown = True
        text = (f"보관함에 저장하지 않은 완료 결과가 {unsaved}건 있습니다. "
                "닫힌 창의 결과는 다시 열 수 없습니다.")
        # Prefer the host window's inline notice so the warning lands where
        # the user still is; a standalone dialog gets a non-modal box so the
        # close itself is never held back either way.
        notify = getattr(self.parent(), "_show_notice", None)
        if callable(notify):
            try:
                notify(text)
                return
            except Exception:  # noqa: BLE001 - the notice must not break closing
                pass
        msg_box = _facade_attr("QMessageBox", QMessageBox)
        box = msg_box(msg_box.Icon.Information, "저장하지 않은 결과 안내",
                      text, msg_box.StandardButton.Ok)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.show()
        self._notice_box = box

    def closeEvent(self, event):
        self._result_edit_timer.stop()
        self._flow_generation += 1  # the issuing context is gone with the window
        self._notice_before_hide()
        super().closeEvent(event)

    def done(self, result):
        self._result_edit_timer.stop()
        self._flow_generation += 1
        # ESC/reject path hides the dialog without a closeEvent — same notice.
        self._notice_before_hide()
        super().done(result)

    def _toggle_render(self, rendered):
        if rendered:
            self.render_view.setHtml(render_prompt_html(self.result_edit.toPlainText()))
        self.render_view.setVisible(rendered)
        self.result_edit.setVisible(not rendered)
        self.view_toggle.setText("편집으로 돌아가기" if rendered else "문서 형태로 보기")

    def _on_result_edit_settled(self) -> None:
        if self.pages.currentIndex() != self.PAGE_RESULT:
            return
        self._sync_result_record()
        if self.view_toggle.isChecked():
            self.render_view.setHtml(render_prompt_html(self.result_edit.toPlainText()))
        self._refresh_result_helpers()

    def _refresh_result_helpers(self) -> None:
        text = self.result_edit.toPlainText()
        rec = self._step_results.get(self._result_view_index)
        original = rec["original"] if rec else self._last_original
        context = "\n".join(rec.get("revisions", [])) if rec else ""
        self.slot_fill_bar.refresh_slots(text)
        self.quality_bar.update_report(original, context, text)
        summary = summarize_pii(text) if text.strip() else ""
        self.mask_result_pii_button.setVisible(bool(summary))

    def _apply_slot_fills(self, updated_text: str) -> None:
        from .assembler import extract_fillable_slots
        self.result_edit.setPlainText(updated_text)
        self._sync_result_record()
        if self.view_toggle.isChecked():
            self.render_view.setHtml(render_prompt_html(updated_text))
        if not extract_fillable_slots(updated_text):
            rec = self._step_results.get(self._result_view_index)
            if rec is not None and rec.get("total", 0) > 0:
                rec["answered"] = rec["total"]
            self.complete_label.setText("완성도 100%")
            self.complete_label.setStyleSheet("font-weight:600;color:#2f6b3a;")
        self.result_status.setText("채운 내용을 프롬프트에 반영했어요.")
        self._refresh_result_helpers()

    def _append_missing_conditions(self, lines: str) -> None:
        current = self.result_edit.toPlainText()
        marker = "\n\n[과업]"
        insertion = f"\n{lines}"
        if marker in current:
            updated = current.replace(marker, insertion + marker, 1)
        else:
            updated = current.rstrip() + insertion
        self.result_edit.setPlainText(updated)
        self._sync_result_record()
        if self.view_toggle.isChecked():
            self.render_view.setHtml(render_prompt_html(updated))
        self.result_status.setText("빠졌던 원문 조건을 맥락에 보충했어요.")
        self._refresh_result_helpers()

    def _refresh_alt_types(self, type_id: str, enabled: bool = True) -> None:
        while self.alt_type_layout.count():
            item = self.alt_type_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        alts = alternative_types(type_id)
        if not alts or not enabled:
            self.alt_type_row.hide()
            return
        lbl = QLabel("다른 형식으로 바꾸기:")
        lbl.setObjectName("flowMuted")
        self.alt_type_layout.addWidget(lbl)
        for alt_id, alt_label in alts:
            btn = build_chip_button(alt_label)
            btn.setToolTip(f"답변한 내용을 유지한 채 '{alt_label}' 구조로 다시 만듭니다.")
            btn.clicked.connect(lambda _c=False, tid=alt_id: self._switch_result_type(tid))
            self.alt_type_layout.addWidget(btn)
        self.alt_type_layout.addStretch()
        self.alt_type_row.show()

    def _switch_result_type(self, alt_type_id: str) -> None:
        if self._result_view_index != self._segment_index:
            return
        self._sync_counter()
        new_diag = diagnose(self._last_original, alt_type_id)
        self._carry_over_answers_to_diag(new_diag)
        self._diag = new_diag
        if self._segments and 0 <= self._segment_index < len(self._segments):
            self._segments[self._segment_index]["type"] = alt_type_id
        prompt = build_prompt(
            alt_type_id, self._last_original,
            self._diag["questions"], self._answers,
            self._diag["extracted"], self._collect_tuner(),
            seeded=self._diag.get("seeded_topics"),
        )
        self._show_result(prompt)
        self.result_status.setText(f"'{_TYPE_BY_ID[alt_type_id]['label']}' 형식으로 다시 구성했어요.")

    def _apply_refine_preset(self, instruction: str) -> None:
        self.refine_input.setText(instruction)
        self.refine_input.setFocus()

    def _apply_to_chat(self) -> None:
        self._sync_result_record()
        index = self._result_view_index
        rec = self._step_results.get(index)
        prompt = rec["original"] if rec else self._last_original
        label = rec["label"] if rec else self._diag["label"]
        result = self.result_edit.toPlainText()
        if not result.strip():
            self.result_status.setText("대화창으로 보낼 내용이 없어요.")
            return
        self._accept_if_done(result, label, prompt)
        self.apply_to_chat_button.setText("넣음")
        self.result_status.setText("완성형 프롬프트를 대화창 결과 목록에 넣었어요.")
        self._defer(1500, lambda: self.apply_to_chat_button.setText("💬 대화창에 바로 넣기"))

    def _export_document(self) -> None:
        text = self.result_edit.toPlainText()
        if not text.strip():
            self.result_status.setText("저장할 프롬프트가 없어요.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "완성형 프롬프트 문서로 저장",
            "완성형_프롬프트.hwpx",
            "한글 문서 (*.hwpx);;텍스트 문서 (*.txt);;마크다운 문서 (*.md);;HTML 문서 (*.html)",
        )
        if not path:
            return
        try:
            from syeonpet.work.doc_export import export_deliverable
            export_deliverable(text, path, title="단계별 완성형 프롬프트")
            self.result_status.setText(f"문서 파일로 저장했어요: {Path(path).name}")
        except Exception as exc:
            self.result_status.setText(f"문서 저장에 실패했어요: {exc}")

    def _mask_result_pii(self) -> None:
        raw = self.result_edit.toPlainText()
        if not raw.strip():
            return
        masked = mask_pii(raw)
        self.result_edit.setPlainText(masked)
        self._sync_result_record()
        if self.view_toggle.isChecked():
            self.render_view.setHtml(render_prompt_html(masked))
        self._refresh_result_helpers()
        self.result_status.setText("프롬프트 안의 개인정보를 가렸어요.")

    def _next_segment(self):
        if self._segment_index + 1 < len(self._segments):
            self._result_edit_timer.stop()
            self._sync_result_record()
            self._segment_index += 1
            self._begin_segment()

    def _restart(self):
        # Abandoning the request abandons its context too — a late AI reply
        # must not land on whatever comes next.
        self._result_edit_timer.stop()
        self._flow_generation += 1
        # Same tuner isolation as a new segment — old chips/free text die here.
        self._sync_result_record()
        self._tuner_widgets = []
        self.tuner_free = None
        self.input_edit.clear()
        self.pages.setCurrentIndex(self.PAGE_INPUT)
        self.input_edit.setFocus()

    def _clear_preferences(self):
        try:
            PreferenceStore().clear(self._diag["type"])
        except OSError:
            pass
        self.pref_clear.hide()
        self.counter_label.setText("기억한 답변을 지웠어요. " + self.counter_label.text())

    # ----- output actions --------------------------------------------------

    def _copy(self):
        if copy_to_clipboard(self.result_edit.toPlainText()):
            self.copy_button.setText("복사됨")
            self.result_status.setText("프롬프트를 복사했어요. 열린 화면에 붙여 넣어 쓰면 돼요.")
            self._defer(1500, lambda: self.copy_button.setText("프롬프트 복사"))
        else:
            self.result_status.setText("복사하지 못했어요. 다시 시도해 주세요.")

    def _save(self):
        from optimizer_library import LibraryStore
        index = self._result_view_index
        rec = self._step_results.get(index)
        viewing_current = index == self._segment_index
        prompt = rec["original"] if rec else self._last_original
        label = rec["label"] if rec else self._diag["label"]
        result = self.result_edit.toPlainText()
        if not prompt.strip() or not result.strip():
            self.result_status.setText("저장할 내용이 없어요.")
            return
        title = ""
        for ch in prompt.strip():
            if utf16_length(title + ch) > 120:
                break
            title += ch
        # The library accepts revision entries up to 2,000 UTF-16 units —
        # clip longer answers instead of letting the save fail outright.
        if rec is not None:
            raw_revisions = rec["revisions"]
        else:
            raw_revisions = [f"{card.question['title']}: {card.value()}"
                             for card in self._cards if card.value()]
        revisions, clipped = [], False
        for entry in raw_revisions:
            if utf16_length(entry) > 2000:
                entry = _clip_utf16(entry, 2000)
                clipped = True
            revisions.append(entry)
            if len(revisions) >= 30:
                break
        item = {"id": str(uuid.uuid4()), "title": title, "prompt": prompt,
                "result": result, "task": "writing", "detail": "detailed",
                "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "mode": label, "archived": False, "stale": False,
                "syeonRevisions": revisions}
        try:
            LibraryStore().merge([item])
        except (OSError, ValueError) as exc:
            self.result_status.setText(f"저장하지 못했어요: {exc}")
            return
        if rec is not None:
            rec["text"] = result
            rec["saved_text"] = result
        self._update_done_row(index)
        self._update_unsaved_notice(index)
        self.save_button.setText("저장됨")
        status = ("보관함에 저장했어요. 보관함에서 다시 열 수 있어요." if viewing_current
                  else f"{index + 1}단계 결과를 보관함에 저장했어요. 보관함에서 다시 열 수 있어요.")
        self.result_status.setText(status + (" 긴 답변은 잘려 저장됐어요." if clipped else ""))
        self._defer(1500, lambda: self.save_button.setText("보관함에 저장"))
        self._accept_if_done(result, label, prompt)

    def _accept_if_done(self, text, label, original):
        if self._on_finished is not None:
            try:
                # Register the text as the user sees it (post-edit), matching
                # what was just saved to the library.
                self._on_finished(text, label, original)
            except Exception:
                pass

    def _open_external(self, key):
        text = self.result_edit.toPlainText()
        copied = open_external(key, text)
        name = dict((k, n) for k, n, _u in EXTERNAL_AI)[key]
        pii_warn = " ⚠️ 개인정보가 포함돼 있는지 확인해 주세요." if summarize_pii(text) else ""
        self.result_status.setText(
            f"{name}을(를) 열었어요. 내용이 자동으로 들어가지 않으면 Ctrl+V로 직접 붙여 주세요.{pii_warn}"
            if copied else f"{name}을(를) 열었어요. 복사가 안 됐으면 '프롬프트 복사'로 직접 복사해 주세요.{pii_warn}")

    # ----- optional AI paths (consent-gated) --------------------------------

    def _ai_send(self, kind, work):
        if self._busy_ai:
            return
        self._busy_ai = True
        self._ai_kind = kind
        # Tag the request with the context it was issued under so a late
        # reply can be dropped instead of bleeding into another segment.
        self._ai_context = (self._segment_index, self._flow_generation)
        self.ai_question_button.setEnabled(False)
        self.refine_button.setEnabled(False)
        self.generate_button.setEnabled(False)  # block generation while AI is in flight

        def run():
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - sanitized below
                try:
                    self._signals.failed.emit(str(exc))
                except RuntimeError:
                    pass
            else:
                try:
                    self._signals.done.emit(result)
                except RuntimeError:
                    pass

        threading.Thread(target=run, daemon=True, name="flow-ai").start()

    def _ask_ai_questions(self):
        provider, profile = self._provider_getter()
        if provider == "offline" or self._busy_ai:
            return
        existing = [q["title"] for q in self._diag["questions"]]
        detail_text = (f"원래 요청:\n{self._last_original}\n\n"
                       f"작업 유형: {self._diag['label']}\n\n"
                       f"이미 묻는 질문:\n" + ("\n".join(existing) if existing else "(없음)"))
        self._ai_confirm(
            provider, profile,
            "원래 요청, 현재 진단 정보와 이미 묻는 질문 목록을",
            detail_text,
            lambda: self._ai_questions_confirmed(provider, profile, existing))

    def _ai_confirm(self, provider, profile, sends, detail_text, callback):
        """Shared-wording consent box built on this module's QMessageBox.

        Construction stays on the module-level name so tests can patch it;
        the text, notices and button labels come from the shared helper.
        """
        from syeonpet.chat.dialogs import send_confirm_text
        local = _is_local(profile)
        service = _service_label(provider, profile)
        pii_summary = summarize_pii(detail_text)
        extra_notice = (
            f"주의: {pii_summary}가 감지되었습니다. 실명·식별정보를 '🛡️ 개인정보 가리기'로 치환하는 것을 권장합니다."
            if pii_summary else ""
        )
        msg_box = _facade_attr("QMessageBox", QMessageBox)
        box = msg_box(msg_box.Icon.Question, _ai_confirm_title(profile),
                      send_confirm_text(provider, service, sends,
                                        extra_notice=extra_notice, local=local),
                      msg_box.StandardButton.Yes | msg_box.StandardButton.No, self)
        box.setDefaultButton(msg_box.StandardButton.No)
        box.finished.connect(
            lambda value: callback() if value == msg_box.StandardButton.Yes else None)
        if hasattr(box, "setTextFormat"):  # real QMessageBox; the test stub stops here
            sync_topmost_parent(box, self)
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setDetailedText(detail_text)
            box.finished.connect(lambda _v: box.setDetailedText(""))
            yes = box.button(msg_box.StandardButton.Yes)
            if yes is not None:
                yes.setText("처리 시작" if local else f"{service}로 전송")
            cancel = box.button(msg_box.StandardButton.No)
            if cancel is not None:
                cancel.setText("취소")
                cancel.setFocus()
            box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        box.open()

    def _ai_questions_confirmed(self, provider, profile, existing):
        self.ai_question_status.setText("AI가 보완 질문을 만드는 중…")
        self.ai_question_status.show()

        def work():
            from optimizer_ai import ask_ai
            payload = {"original_request": self._last_original,
                       "task_type": self._diag["label"],
                       "already_asked": existing}
            text = ask_ai(provider, _QUESTION_HINTS, payload, profile=profile)
            lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
                     for line in text.splitlines()]
            return [line for line in lines if 8 <= len(line) <= 80 and line not in existing][:2]

        self._ai_send("questions", work)

    def _refine(self):
        # Refine only ever targets the live segment's result — a finished
        # step's record stays read-only for this path.
        if self._result_view_index != self._segment_index:
            return
        provider, profile = self._provider_getter()
        if provider == "offline" or self._busy_ai:
            self.result_status.setText("다듬기 재생성은 AI 정리 방식이 필요해요. 대화창의 정리 방식에서 AI를 선택해 주세요.")
            return
        instruction = self.refine_input.text().strip()
        if not instruction:
            self.result_status.setText("다듬기 방법을 한 줄로 적어 주세요.")
            return
        detail_text = (f"완성형 프롬프트:\n{self.result_edit.toPlainText()}\n\n"
                       f"다듬기 지시:\n{instruction}")
        self._ai_confirm(
            provider, profile,
            "완성형 프롬프트와 다듬기 지시를",
            detail_text,
            lambda: self._refine_confirmed(provider, profile, instruction))

    def _refine_confirmed(self, provider, profile, instruction):
        previous = self.result_edit.toPlainText()
        context = "\n".join(f"{card.question['title']}: {card.value()}"
                            for card in self._cards if card.value())
        self.result_status.setText("AI로 다듬는 중… 응답에 1분 이상 걸릴 수 있어요.")

        def work():
            from optimizer_ai import optimize_ai
            reply = optimize_ai(self._last_original, context=context, provider=provider,
                                revision=instruction, previous_result=previous,
                                task="writing", detail="detailed",
                                profile=profile)
            return reply["result"]

        self._ai_send("refine", work)

    def _restore_ai_controls(self):
        """Re-enable the controls an in-flight AI call had frozen.

        Refine stays bound to the live segment's result: while a finished
        step is being read, both its input and button remain locked — the
        same gate ``_render_record`` applies.
        """
        self.ai_question_button.setEnabled(True)
        self.generate_button.setEnabled(True)
        provider, _profile = self._provider_getter()
        viewing_current = self._result_view_index == self._segment_index
        self.refine_button.setEnabled(viewing_current and provider != "offline")

    def _ai_context_is_stale(self):
        """True when the in-flight request was issued under another context."""
        return (self._segment_index, self._flow_generation) != self._ai_context

    def _discard_stale_ai(self):
        """A late reply must not move the user or write into a new record."""
        self.ai_question_status.setText(
            "이전 단계에 요청한 AI 응답이 늦게 도착해 반영하지 않았어요.")
        self.ai_question_status.show()

    def _ai_done(self, result):
        self._busy_ai = False
        self._restore_ai_controls()
        if self._ai_context_is_stale():
            self._discard_stale_ai()
            return
        if self._ai_kind == "questions":
            if not result:
                self.ai_question_status.setText("추가로 물을 만한 질문을 찾지 못했어요.")
                return
            base = len(self._cards)
            for offset, question_text in enumerate(result, 1):
                topic = _topic(f"ai_extra_{offset}", question_text,
                               "AI가 이 요청에서 더 필요하다고 본 정보예요.",
                               [], "추가 확인", optional=True)
                card = _QuestionCard(base + offset, topic)
                card.answer.textChanged.connect(self._sync_counter)
                self.questions_layout.addWidget(card)
                self._cards.append(card)
            self.ai_question_status.setText(f"AI 보완 질문 {len(result)}개를 아래에 붙였어요.")
            self._sync_counter()
        else:
            self._show_result(result, refined=True)
            self.result_status.setText("다듬은 결과를 확인해 주세요. AI 결과이므로 사용 전 검토가 필요해요.")

    def _ai_failed(self, message):
        from syeonpet.chat.error_sanitizer import sanitize_error
        self._busy_ai = False
        self._restore_ai_controls()
        if self._ai_context_is_stale():
            self._discard_stale_ai()
            return
        safe = "연결 실패 또는 처리 중 오류가 발생했습니다. 인터넷 연결과 설정을 확인하고 다시 시도해 주세요."
        if message in _KNOWN_AI_ERRORS or sanitize_error(message) == message:
            safe = message
        if self._ai_kind == "questions":
            self.ai_question_status.setText("보완 질문을 만들지 못했어요: " + safe)
        else:
            self.result_status.setText("다듬지 못했어요: " + safe + "\n이전 결과는 그대로 남아 있습니다.")


_QUESTION_HINTS = (
    "당신은 교육기관 직원의 업무 요청을 보강하는 도우미입니다.\n"
    "입력 JSON의 original_request(원래 요청)와 task_type(업무 유형), already_asked(이미 묻는 질문)을 보고, "
    "완성형 프롬프트를 만들기 위해 더 꼭 필요한 확인 질문을 최대 2개만 제안하세요.\n"
    "- 이미 묻는 질문과 겹치지 않는 것만 고릅니다.\n"
    "- 질문만 한 줄에 하나씩 출력합니다. 번호·머리말·설명은 쓰지 않습니다.\n"
    "- 한국어로 씁니다. 질문은 80자 이내로 짧게 씁니다.\n"
    "- 요청에 없는 사실을 지어내지 않고, 묻는 형태로만 씁니다."
)

_KNOWN_AI_ERRORS = {
    "모델 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
    "AI 서비스에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요.",
    "AI 요청에 실패했습니다.", "정리 요청을 취소했습니다.",
    "서비스 사용량 제한에 도달했습니다 (HTTP 429). 잠시 후 다시 시도해 주세요.",
}


def show_flow_dialog(parent=None, initial_text="", provider_getter=None, on_finished=None) -> FlowDialog:
    """Open without a nested event loop; callers keep the returned reference."""
    dialog = FlowDialog(parent, initial_text, provider_getter, on_finished)
    dialog.show()
    return dialog
