"""Unified 4-Section Workspace Hub positioned beside the desktop pet."""
from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QSettings, QTimer, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from optimizer_chat import PromptConversation
from syeonpet.finder.finder_ui import FileFinderWidget
from syeonpet.finder.index_engine import FileIndex, build_index
from syeonpet.work.bag_ui import BagWorkspaceWidget
from syeonpet.privacy.detector import detect_pii, mask_pii

try:
    from syeonpet.chat.direct_work_ui import DirectWorkWidget
except Exception:
    DirectWorkWidget = None


class ChatPanel(QWidget):
    """Unified 4-section assistant hub: Work, Finder, Bag, and Prompt."""

    MIN_OPACITY = 60
    MAX_OPACITY = 100
    companion_praise = Signal(str)

    def __init__(self, pet, behavior, cfg: dict, prompt_open=None, settings=None):
        super().__init__()
        self.pet = pet
        self.behavior = behavior
        self.cfg = cfg
        self.setObjectName("promptWindow")
        self.setWindowTitle("🌱 션 데스크탑 비서")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(360, 450)
        self.resize(460, 650)

        self.setStyleSheet("""
            QWidget#promptWindow { background: #f3f6fa; }
            QLabel#hubHeaderTitle { font-size: 13pt; font-weight: bold; color: #1e3a52; }
            QLabel#windowControlLabel { color: #344e60; font-size: 9pt; }
            QPushButton#pinBtn { border: 1px solid #c0d1de; border-radius: 4px; padding: 3px 6px; background: #fff; font-size: 9pt; }
            QPushButton#pinBtn:checked { background: #e0f2fe; border-color: #38bdf8; color: #0369a1; font-weight: bold; }
            QPushButton#sectionPill { border: 1px solid #d0dfea; border-radius: 14px; padding: 6px 12px; background: #ffffff; color: #2c4253; font-weight: bold; font-size: 9.5pt; }
            QPushButton#sectionPill:hover { background: #eaf3f9; border-color: #adc6db; }
            QPushButton#sectionPill:checked { background: #2563eb; color: #ffffff; border-color: #1d4ed8; }
            QPushButton#wakePet { background: #ffffff; color: #172b3a; border: 1px solid #8fa5b4; border-radius: 4px; padding: 4px 7px; }
            QPushButton#wakePet:focus { border: 2px solid #195b83; padding: 3px 6px; }
        """)

        self._settings = settings if settings is not None else QSettings("SyeonPet", "Conversation")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 8)
        layout.setSpacing(5)

        # 1. Top navigation & accessibility row
        top_bar = QHBoxLayout()
        header_title = QLabel("🌱 션 데스크탑 비서", self)
        header_title.setObjectName("hubHeaderTitle")
        top_bar.addWidget(header_title)
        top_bar.addStretch()

        # Font size combo
        top_bar.addWidget(QLabel("글자 크기", self, objectName="windowControlLabel"))
        self.font_size_combo = QComboBox(self)
        self.font_size_combo.addItems(["보통", "크게", "아주 크게"])
        saved_font = str(self._settings.value("fontSizeChoice", "보통"))
        idx = self.font_size_combo.findText(saved_font)
        if idx >= 0:
            self.font_size_combo.setCurrentIndex(idx)
        self.font_size_combo.currentTextChanged.connect(self._on_font_size_changed)
        top_bar.addWidget(self.font_size_combo)

        # Pin always on top toggle button
        self.pin_button = QPushButton("📌 항상 위", self)
        self.pin_button.setObjectName("pinBtn")
        self.pin_button.setCheckable(True)
        self.pin_button.setChecked(True)
        self.pin_button.toggled.connect(self._toggle_pin)
        top_bar.addWidget(self.pin_button)

        # Opacity slider
        top_bar.addWidget(QLabel("창 불투명도", self, objectName="windowControlLabel"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.opacity_slider.setAccessibleName("대화창 불투명도")
        self.opacity_slider.setRange(self.MIN_OPACITY, self.MAX_OPACITY)
        self.opacity_slider.setMaximumWidth(95)
        self.opacity_slider.setSingleStep(5)
        self.opacity_value = QLabel(self, objectName="windowControlLabel")
        self.opacity_value.setMinimumWidth(self.opacity_value.fontMetrics().horizontalAdvance("100%"))
        top_bar.addWidget(self.opacity_slider)
        top_bar.addWidget(self.opacity_value)
        layout.addLayout(top_bar)

        # 2. Section pill buttons row
        pill_bar = QHBoxLayout()
        pill_bar.setSpacing(6)
        self.section_buttons: dict[str, QPushButton] = {}
        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)

        sections = [
            ("work", "✨ 바로 도움받기"),
            ("finder", "🔍 내 파일 찾기"),
            ("bag", "🎒 할 일 · 메모"),
            ("prompt", "🛠️ AI 질문 다듬기"),
        ]
        for key, title in sections:
            btn = QPushButton(title, self)
            btn.setObjectName("sectionPill")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, k=key: self.open_section(k))
            self.section_buttons[key] = btn
            self.button_group.addButton(btn)
            pill_bar.addWidget(btn)
        layout.addLayout(pill_bar)

        # 3. Sleep notice row
        sleep_row = QHBoxLayout()
        self.sleep_notice = QLabel("션이 완전히 자고 있어요. 깨우면 비서를 이어갈 수 있어요.", self, objectName="windowControlLabel")
        self.sleep_notice.setWordWrap(True)
        self.wake_button = QPushButton("션 깨우기", self, objectName="wakePet")
        self.wake_button.setAccessibleName("잠든 션 깨우기")
        self.wake_button.clicked.connect(self.behavior.wake)
        sleep_row.addWidget(self.sleep_notice, 1)
        sleep_row.addWidget(self.wake_button)
        layout.addLayout(sleep_row)

        # 4. QStackedWidget for 4 sections
        self.section_stack = QStackedWidget(self)

        # Page 0: Direct Work
        if DirectWorkWidget is not None:
            self.work_widget = DirectWorkWidget(self)
        else:
            self.work_widget = QWidget(self)
        self.section_stack.addWidget(self.work_widget)

        # Page 1: File Finder (instantiated with empty roots for instantaneous startup; scanned on first open)
        self.finder_widget = FileFinderWidget(index=FileIndex([]), parent=self)
        self.finder_widget.send_to_assistant.connect(self._on_finder_send_to_assistant)
        self.section_stack.addWidget(self.finder_widget)

        # Page 2: Bag Workspace
        self.bag_widget = BagWorkspaceWidget(parent=self)
        self.bag_widget.task_completed.connect(self._on_bag_task_completed)
        self.section_stack.addWidget(self.bag_widget)

        # Page 3: Prompt Conversation
        self.prompt_conversation = PromptConversation(self)
        self.conversation_scroll = QScrollArea(self)
        self.conversation_scroll.setObjectName("conversationScroll")
        self.conversation_scroll.setAccessibleName("프롬프트 대화 영역")
        self.conversation_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.conversation_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.conversation_scroll.setWidgetResizable(True)
        self.conversation_scroll.setStyleSheet("""
            QScrollArea#conversationScroll { background: #f3f6fa; border: none; }
            QScrollBar:vertical { background: #e3ebf0; width: 12px; margin: 0; }
            QScrollBar::handle:vertical { background: #6b8494; min-height: 24px; border-radius: 5px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: #e3ebf0; }
        """)
        self.conversation_scroll.setWidget(self.prompt_conversation)
        self.section_stack.addWidget(self.conversation_scroll)

        layout.addWidget(self.section_stack, 1)

        # Wiring and events
        self.prompt_conversation.notice_raised.connect(lambda: QTimer.singleShot(0, self._reveal_prompt_notice))
        self.prompt_conversation.result_ready.connect(lambda: QTimer.singleShot(0, self._show_result))
        self.behavior.fully_asleep_changed.connect(self._sleep_changed)
        self._sleep_changed(self.behavior.fully_asleep)

        try:
            saved_op = int(self._settings.value("opacityPercent", self.MAX_OPACITY))
        except (TypeError, ValueError, OverflowError):
            saved_op = self.MAX_OPACITY
        self.opacity_slider.setValue(max(self.MIN_OPACITY, min(self.MAX_OPACITY, saved_op)))
        self._apply_opacity(self.opacity_slider.value(), persist=False)
        self.opacity_slider.valueChanged.connect(self._set_opacity)

        # Keyboard tab order between controls
        QWidget.setTabOrder(self.opacity_slider, self.wake_button)
        QWidget.setTabOrder(self.wake_button, self.prompt_conversation.provider)
        for btn in self.section_buttons.values():
            btn.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.font_size_combo.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.pin_button.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        default_sec = cfg.get("default_section", "prompt")
        self.open_section(default_sec)
        self._apply_font_size(self.font_size_combo.currentText())

    def open_section(self, key: str = "work") -> None:
        """Switch section stack to target view and update pill button state."""
        if key == "work" and DirectWorkWidget is None:
            key = "prompt"
        if key == "finder" and getattr(self, "finder_widget", None) is not None:
            if not getattr(self.finder_widget, "_indexed", False) and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                self.finder_widget._indexed = True
                self.finder_widget.start_async_indexing()
        idx = {"work": 0, "finder": 1, "bag": 2, "prompt": 3}.get(key, 0)
        self.section_stack.setCurrentIndex(idx)
        btn = self.section_buttons.get(key)
        if btn:
            btn.setChecked(True)

    def _toggle_pin(self, checked: bool) -> None:
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def _on_font_size_changed(self, choice: str) -> None:
        self._settings.setValue("fontSizeChoice", choice)
        self._apply_font_size(choice)

    def _apply_font_size(self, choice: str) -> None:
        pt = {"보통": 10, "크게": 12, "아주 크게": 14}.get(choice, 10)
        font = self.font()
        font.setPointSize(pt)
        self.setFont(font)
        for w in (self.prompt_conversation, getattr(self, "work_widget", None),
                  getattr(self, "finder_widget", None), getattr(self, "bag_widget", None)):
            if w is not None:
                w.setFont(font)
        self.prompt_conversation.set_font_scale(pt)
        if hasattr(self.work_widget, "set_font_scale"):
            self.work_widget.set_font_scale(pt)

    def _on_bag_task_completed(self, title: str) -> None:
        self.companion_praise.emit(f"할 일 완료! '{title}' 정말 멋져요 ✨")

    def _on_finder_send_to_assistant(self, file_name: str, extracted_text: str, badge: str) -> None:
        text = mask_pii(extracted_text) if detect_pii(extracted_text) else extracted_text
        if DirectWorkWidget is not None and hasattr(self.work_widget, "load_from_file_finder"):
            self.work_widget.load_from_file_finder(file_name, text, badge)
            self.open_section("work")
        elif DirectWorkWidget is not None and hasattr(self.work_widget, "load_document"):
            self.work_widget.load_document(file_name, text, badge)
            self.open_section("work")
        else:
            self.prompt_conversation.input.setPlainText(text)
            self.open_section("prompt")
        self.show_near_pet()

    def _reveal_prompt_notice(self):
        self.prompt_conversation.layout().activate()
        self.conversation_scroll.ensureWidgetVisible(self.prompt_conversation.inline_notice, 0, 12)

    def _apply_opacity(self, value: int, persist: bool):
        val = max(self.MIN_OPACITY, min(self.MAX_OPACITY, int(value)))
        self.setWindowOpacity(val / 100.0)
        self.opacity_value.setText(f"{val}%")
        if persist:
            self._settings.setValue("opacityPercent", val)

    def _set_opacity(self, value: int):
        self._apply_opacity(value, persist=True)

    def _show_result(self):
        result = self.prompt_conversation.result
        viewport = self.conversation_scroll.viewport()
        y = result.mapTo(viewport, QPoint(0, 0)).y()
        if y < 0 or y + result.height() > viewport.height():
            header_y = self.prompt_conversation.result_choice.mapTo(self.prompt_conversation, QPoint(0, 0)).y()
            self.conversation_scroll.verticalScrollBar().setValue(max(0, header_y - 8))
            inp = self.prompt_conversation.input
            inp_y = inp.mapTo(viewport, QPoint(0, 0)).y()
            if inp.hasFocus() and (inp_y < 0 or inp_y + inp.height() > viewport.height()):
                result.setFocus()
                self.conversation_scroll.verticalScrollBar().setValue(max(0, header_y - 8))

    def _sleep_changed(self, paused: bool):
        self.sleep_notice.setVisible(paused)
        self.wake_button.setVisible(paused)
        self.prompt_conversation.set_sleep_paused(paused)

    @property
    def is_busy(self) -> bool:
        return self.prompt_conversation.is_busy

    def has_pending_work(self) -> bool:
        return self.prompt_conversation.has_pending_work()

    def export_update_state(self):
        return {"schema": 1, "visible": self.isVisible(),
                "conversation": self.prompt_conversation.export_update_state()}

    def restore_update_state(self, state):
        if (not isinstance(state, dict) or type(state.get("schema")) is not int
                or state["schema"] != 1 or type(state.get("visible")) is not bool):
            raise ValueError("복원할 대화창의 형식을 확인할 수 없습니다.")
        self.prompt_conversation.restore_update_state(state.get("conversation"))
        return state["visible"]

    def request_app_quit(self, callback, preserve_for_update=False):
        if preserve_for_update and self.is_busy:
            self.prompt_conversation.status.setText(
                "진행 중인 정리가 끝난 뒤 업데이트를 적용할 수 있어요. 입력과 결과는 그대로 유지됩니다.")
            self.prompt_conversation._show_notice("진행 중인 정리가 끝난 뒤 업데이트를 적용해 주세요.")
            return
        if not self.is_busy and not self.has_pending_work():
            callback()
            return
        if getattr(self, "_quit_dialog", None) is not None:
            return
        if not self.isVisible():
            self.show_near_pet()
        conv = self.prompt_conversation
        snapshot = conv.quit_snapshot()
        was_busy = self.is_busy
        msg = (("업데이트를 위해 현재 대화와 작성 중인 내용을 이 기기에 보관하고 "
                "다음 실행에서 복원합니다. 보관에 실패하면 종료하지 않습니다.")
               if preserve_for_update else
               ("종료하면 현재 대화와 작성 중인 내용이 사라집니다. 필요한 내용은 보관함에 저장하거나 복사해 주세요."))
        if was_busy:
            msg += "\n진행 중인 요청도 중단될 수 있습니다."
        dialog = QMessageBox(QMessageBox.Icon.Question, "션 종료", msg,
                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        self._quit_dialog = dialog

        def finished(res):
            self._quit_dialog = None
            if res != QMessageBox.StandardButton.Yes:
                return
            if self.is_busy != was_busy or conv.quit_snapshot() != snapshot:
                conv.status.setText("확인 중 대화 상태가 바뀌어 종료하지 않았어요. 내용을 다시 확인해 주세요.")
                return
            callback()

        dialog.finished.connect(finished)
        dialog.open()

    @staticmethod
    def _screen_for_pet(geo):
        return QGuiApplication.screenAt(geo.center()) or QGuiApplication.primaryScreen()

    def _fit_near_pet(self, geo, bounds):
        if bounds.isEmpty():
            return
        frame = self.frameGeometry()
        extra_w, extra_h = max(0, frame.width() - self.width()), max(0, frame.height() - self.height())
        margin = 4
        avail_w, avail_h = max(1, bounds.width() - extra_w - 2 * margin), max(1, bounds.height() - extra_h - 2 * margin)
        self.setMinimumSize(min(360, avail_w), min(450, avail_h))
        self.resize(min(self.width(), avail_w), min(self.height(), avail_h))
        frame = self.frameGeometry()
        x = geo.right() + 8
        if x + frame.width() > bounds.right() + 1 - margin:
            x = geo.left() - 8 - frame.width()
        x = max(bounds.left() + margin, min(x, bounds.right() + 1 - frame.width() - margin))
        y = max(bounds.top() + margin, min(geo.top() - 20, bounds.bottom() + 1 - frame.height() - margin))
        self.move(x, y)

    def show_near_pet(self):
        geo = self.pet.sprite_rect_global()
        screen = self._screen_for_pet(geo)
        bounds = screen.availableGeometry() if screen is not None else None
        if bounds is not None:
            self._fit_near_pet(geo, bounds)
        self.show()
        if bounds is not None:
            QTimer.singleShot(0, self, lambda: self._fit_near_pet(geo, bounds))
        self.raise_()
        (self.wake_button if self.behavior.fully_asleep else self.prompt_conversation.input).setFocus()

    def show_section_near_pet(self, key: str = "work") -> None:
        self.open_section(key)
        self.show_near_pet()
        if self.behavior.fully_asleep:
            self.wake_button.setFocus()
        elif key == "work" and hasattr(self.work_widget, "input"):
            self.work_widget.input.setFocus()
        elif key == "finder" and hasattr(self.finder_widget, "search_input"):
            self.finder_widget.search_input.setFocus()
        else:
            self.prompt_conversation.input.setFocus()

    def show_prompt_near_pet(self):
        """Show the prompt conversation section near Syeon."""
        self.show_section_near_pet("prompt")
