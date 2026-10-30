"""Prompt conversation window positioned beside the desktop pet."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSettings, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QScrollArea, QSlider, QVBoxLayout, QWidget)

from optimizer_chat import PromptConversation


class ChatPanel(QWidget):
    """One editable conversation; closing the window leaves its draft intact."""

    MIN_OPACITY = 60
    MAX_OPACITY = 100

    def __init__(self, pet, behavior, cfg: dict, prompt_open=None, settings=None):
        super().__init__()
        self.pet = pet
        self.behavior = behavior
        self.setObjectName("promptWindow")
        self.setStyleSheet("""
            QWidget#promptWindow { background: #f3f6fa; }
            QLabel#windowControlLabel { color: #344e60; }
            QPushButton#wakePet { background: #ffffff; color: #172b3a;
                border: 1px solid #8fa5b4; border-radius: 4px; padding: 4px 7px; }
            QPushButton#wakePet:focus { border: 2px solid #195b83; padding: 3px 6px; }
        """)
        # Kept separate from the existing pet config and prompt library.
        self._settings = settings if settings is not None else QSettings("SyeonPet", "Conversation")

        self.setWindowTitle("션과 프롬프트 정리")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(360, 450)
        self.resize(460, 650)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        opacity_row = QHBoxLayout()
        opacity_row.addStretch()
        opacity_label = QLabel("창 불투명도")
        opacity_label.setObjectName("windowControlLabel")
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setAccessibleName("대화창 불투명도")
        self.opacity_slider.setToolTip("대화창의 불투명도를 조절합니다.")
        self.opacity_slider.setRange(self.MIN_OPACITY, self.MAX_OPACITY)
        self.opacity_slider.setMaximumWidth(120)
        self.opacity_slider.setSingleStep(5)
        self.opacity_value = QLabel()
        self.opacity_value.setObjectName("windowControlLabel")
        self.opacity_value.setMinimumWidth(38)
        opacity_label.setBuddy(self.opacity_slider)
        opacity_row.addWidget(opacity_label)
        opacity_row.addWidget(self.opacity_slider)
        opacity_row.addWidget(self.opacity_value)
        layout.addLayout(opacity_row)

        sleep_row = QHBoxLayout()
        self.sleep_notice = QLabel("션이 완전히 자고 있어요. 깨우면 대화를 이어갈 수 있어요.")
        self.sleep_notice.setObjectName("windowControlLabel")
        self.sleep_notice.setWordWrap(True)
        self.wake_button = QPushButton("션 깨우기")
        self.wake_button.setObjectName("wakePet")
        self.wake_button.clicked.connect(self.behavior.wake)
        sleep_row.addWidget(self.sleep_notice, 1)
        sleep_row.addWidget(self.wake_button)
        layout.addLayout(sleep_row)

        self.prompt_conversation = PromptConversation(self)
        self.conversation_scroll = QScrollArea(self)
        self.conversation_scroll.setObjectName("conversationScroll")
        self.conversation_scroll.setAccessibleName("프롬프트 대화 영역")
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
        self.prompt_conversation.notice_raised.connect(
            lambda: self.conversation_scroll.ensureWidgetVisible(
                self.prompt_conversation.inline_notice, 0, 12))
        self.prompt_conversation.result_ready.connect(
            lambda: QTimer.singleShot(0, self._show_result))
        layout.addWidget(self.conversation_scroll, 1)
        self.behavior.fully_asleep_changed.connect(self._sleep_changed)
        self._sleep_changed(self.behavior.fully_asleep)

        try:
            saved = int(self._settings.value("opacityPercent", self.MAX_OPACITY))
        except (TypeError, ValueError, OverflowError):
            saved = self.MAX_OPACITY
        self.opacity_slider.setValue(max(self.MIN_OPACITY, min(self.MAX_OPACITY, saved)))
        self._apply_opacity(self.opacity_slider.value(), persist=False)
        self.opacity_slider.valueChanged.connect(self._set_opacity)

    def _apply_opacity(self, value: int, persist: bool):
        value = max(self.MIN_OPACITY, min(self.MAX_OPACITY, int(value)))
        self.setWindowOpacity(value / 100.0)
        self.opacity_value.setText(f"{value}%")
        if persist:
            self._settings.setValue("opacityPercent", value)

    def _set_opacity(self, value: int):
        self._apply_opacity(value, persist=True)

    def _show_result(self):
        """Show the result heading and keep keyboard focus on a visible field."""
        result = self.prompt_conversation.result
        viewport = self.conversation_scroll.viewport()
        y = result.mapTo(viewport, QPoint(0, 0)).y()
        if y < 0 or y + result.height() > viewport.height():
            header_y = self.prompt_conversation.result_choice.mapTo(
                self.prompt_conversation, QPoint(0, 0)).y()
            self.conversation_scroll.verticalScrollBar().setValue(max(0, header_y - 8))
            input_widget = self.prompt_conversation.input
            input_y = input_widget.mapTo(viewport, QPoint(0, 0)).y()
            if input_widget.hasFocus() and (input_y < 0 or input_y + input_widget.height() > viewport.height()):
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
        """Capture restart state; callers must securely retain it on failure."""
        return {"schema": 1, "visible": self.isVisible(),
                "conversation": self.prompt_conversation.export_update_state()}

    def restore_update_state(self, state):
        """Restore into a fresh panel and return whether it was open before."""
        if (not isinstance(state, dict) or type(state.get("schema")) is not int
                or state["schema"] != 1 or type(state.get("visible")) is not bool):
            raise ValueError("복원할 대화창의 형식을 확인할 수 없습니다.")
        self.prompt_conversation.restore_update_state(state.get("conversation"))
        return state["visible"]

    def request_app_quit(self, callback, preserve_for_update=False):
        """Confirm before terminating a request or discarding in-memory work."""
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
        conversation = self.prompt_conversation
        snapshot = conversation.quit_snapshot()
        was_busy = self.is_busy
        message = (("업데이트를 위해 현재 대화와 작성 중인 내용을 이 기기에 보관하고 "
                    "다음 실행에서 복원합니다. 보관에 실패하면 종료하지 않습니다.")
                   if preserve_for_update else
                   ("종료하면 현재 대화와 작성 중인 내용이 사라집니다. "
                    "필요한 내용은 보관함에 저장하거나 복사해 주세요."))
        if was_busy:
            message += "\n진행 중인 요청도 중단될 수 있습니다."
        dialog = QMessageBox(QMessageBox.Icon.Question, "션 종료", message,
                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        self._quit_dialog = dialog

        def finished(result):
            self._quit_dialog = None
            if result != QMessageBox.StandardButton.Yes:
                return
            if self.is_busy != was_busy or conversation.quit_snapshot() != snapshot:
                conversation.status.setText("확인 중 대화 상태가 바뀌어 종료하지 않았어요. 내용을 다시 확인해 주세요.")
                return
            callback()

        dialog.finished.connect(finished)
        dialog.open()

    @staticmethod
    def _screen_for_pet(geo):
        return QGuiApplication.screenAt(geo.center()) or QGuiApplication.primaryScreen()

    def _fit_near_pet(self, geo, bounds):
        """Keep the whole native window inside the pet's monitor work area."""
        if bounds.isEmpty():
            return
        frame = self.frameGeometry()
        extra_width = max(0, frame.width() - self.width())
        extra_height = max(0, frame.height() - self.height())
        margin = 4
        available_width = max(1, bounds.width() - extra_width - 2 * margin)
        available_height = max(1, bounds.height() - extra_height - 2 * margin)
        # A fixed minimum can make the window larger than a small work area.
        # The inner scroll area keeps the controls reachable when it shrinks.
        self.setMinimumSize(min(360, available_width), min(450, available_height))
        self.resize(min(self.width(), available_width), min(self.height(), available_height))
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
            # Native title-bar dimensions can change after the window is shown.
            QTimer.singleShot(0, self, lambda: self._fit_near_pet(geo, bounds))
        self.raise_()
        (self.wake_button if self.behavior.fully_asleep else self.prompt_conversation.input).setFocus()

    def show_prompt_near_pet(self):
        """Compatibility for existing tray actions; there is only one view."""
        self.show_near_pet()
