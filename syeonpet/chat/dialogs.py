"""Dialogs and UI helpers for prompt conversation."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QLayout, QLineEdit, QMessageBox, QPushButton, QSizePolicy,
    QSpacerItem, QTextEdit, QVBoxLayout, QWidget,
)

from optimizer_core import utf16_length

CHAT_STYLE = """
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
QWidget#promptConversation > QPushButton { min-height: 28px; }
QWidget#promptConversation > QPushButton#primaryAction { font-weight: 600; }
QWidget#promptConversation > QWidget#fillPanel { background: #eaf1f6; border: 1px solid #9aaebc; border-radius: 4px; }
"""

_CONDITION_LABELS = {"purpose": "목적", "audience": "대상", "format": "형식", "deadline": "기한"}
_ROLE_HEADING = "확정된 문의·답변 역할 (사용자 확인):"
_JARVIS_NOTICE = ("JarvisClaw는 외부 모델 제공자에게 요청을 전달할 수 있고 일부 이용 메타데이터를 "
                  "처리할 수 있습니다. 개인정보와 비공개 자료를 보내지 마세요. "
                  "기관 사용 승인을 확인해 주세요. 개인정보 처리방침: https://api.jarvisclaw.ai/privacy-policy")
_SETTINGS_ERROR = ("AI 연결 설정이나 계정 정보를 읽지 못해 요청을 시작하지 않았어요. "
                   "잠시 후 다시 시도하고, 계속되면 AI 설정을 확인해 주세요.")


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


class _StatusText(QTextEdit):
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


def open_clarification_dialog(widget) -> None:
    if widget._busy:
        return
    from prompt_quality import missing_conditions
    text = widget.original or widget.input.toPlainText()
    fields = missing_conditions(text, widget._clarifications)
    dialog = QDialog(widget)
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
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
    skip = buttons.addButton("건너뛰기", QDialogButtonBox.ButtonRole.RejectRole)
    skip.setAccessibleName("조건 확인 건너뛰기")
    expected = widget._request_snapshot()

    def apply():
        if widget._request_snapshot() != expected:
            widget._show_notice("입력이 바뀌었습니다. 조건을 다시 확인해 주세요.")
            dialog.reject()
            return
        additions = []
        for key, field in answers.items():
            value = field.text().strip()
            if not value:
                continue
            if key == "inquiry_roles":
                additions.append(_ROLE_HEADING + "\n" + value)
            else:
                additions.append(f"{_CONDITION_LABELS.get(key, key)}: {value}")
        if additions:
            candidate = "\n".join(filter(None, [widget._clarifications, *additions]))
            if utf16_length(candidate) > 2000:
                widget._show_notice("추가한 조건이 2,000자를 넘어 적용하지 않았어요. 조건 확인에서 일부를 지운 뒤 다시 적용해 주세요.")
                return
            widget._clarifications = candidate
        dialog.accept()

    buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(apply)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    widget._clarification_dialog = dialog
    widget._clarification_fields = answers
    dialog.open()


def open_comparison_dialog(widget) -> None:
    if widget._busy or widget._sleep_paused:
        return
    dialog = QDialog(widget)
    dialog.setWindowTitle("두 모델 비교")
    layout = QVBoxLayout(dialog)
    choices = []
    for label in ("비교 모델 A", "비교 모델 B"):
        combo = QComboBox()
        combo.setAccessibleName(label)
        for index in range(widget.provider.count()):
            combo.addItem(widget.provider.itemText(index), widget.provider.itemData(index))
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
                profile = widget._resolve_profile(destination)
                pairs.append((profile.id, profile.model, profile.account_id) if profile else (destination, "", None))
            if pairs[0] == pairs[1]:
                raise ValueError("서로 다른 모델 또는 프로필을 선택하세요.")
            text = widget.input.toPlainText()
            if not text.strip():
                raise ValueError("먼저 요청 또는 추가 조건을 입력하세요.")
            labels = [widget._destination(value) for value in destinations]
            profiles = [widget._resolve_profile(value) for value in destinations]
        except ValueError as exc:
            widget._show_notice(str(exc))
            return
        try:
            expected = widget._request_snapshot()
        except (OSError, ValueError):
            widget._show_notice(_SETTINGS_ERROR)
            return
        frozen = widget._generation_input(text)
        for destination in destinations:
            over = widget._over_limit(frozen[1], frozen[2],
                                      frozen[3] if destination != "offline" else frozen[4],
                                      frozen[5], destination)
            if over:
                widget._show_notice(over)
                return
        dialog.accept()
        body = ("같은 입력으로 순서대로 2회 요청합니다.\nA: " + labels[0] + "\nB: " + labels[1]
                + "\n로컬 정리와 외부 전송을 구분해 확인하세요. B에는 A의 결과를 보내지 않습니다.")
        if "jarvisclaw" in destinations:
            body += "\n\n" + _JARVIS_NOTICE
        confirmation = widget._confirm("비교 요청 확인", body,
            lambda: widget._start_comparison(expected, frozen, destinations, profiles))
        if isinstance(confirmation, QMessageBox):
            confirmation.setDetailedText(
                f"원문:\n{frozen[1]}\n\n누적 조건:\n{frozen[3]}\n\n추가 조건:\n{frozen[2]}"
                f"\n\n이전 결과:\n{frozen[5]}\n\n작업 유형: {frozen[6]}\n상세 수준: {frozen[7]}")
            confirmation.finished.connect(lambda _: confirmation.setDetailedText(""))

    start.clicked.connect(begin)
    widget._comparison_dialog = dialog
    widget._comparison_choices = choices
    dialog.open()


def open_template_dialog(widget) -> None:
    from optimizer_templates import show_template_picker
    existing = getattr(widget, "_template_dialog", None)
    if existing is not None:
        try:
            if existing.isVisible():
                existing.raise_()
                existing.activateWindow()
                return
        except RuntimeError:
            pass

    def insert(chosen):
        cursor = widget.input.textCursor()
        cursor.insertText(chosen)
        widget.input.setTextCursor(cursor)
        widget.input.setFocus()

    widget._template_dialog = show_template_picker(widget, insert)


def open_library_dialog(widget) -> None:
    from optimizer_library import LibraryDialog
    from syeonpet.chat.state_codec import _append_conditions
    widget._save_result()
    widget._library_snapshot = widget._draft_snapshot()
    selected_count = (widget._result_revision_counts[widget._shown_result]
                      if 0 <= widget._shown_result < len(widget._result_revision_counts) else 0)
    missing = widget.revisions[selected_count:]
    selected_result = widget.result.toPlainText()
    saved_result = _append_conditions(selected_result, missing) if selected_result else selected_result
    if missing:
        widget.status.setText("이전 회차를 보관할 때 이후 추가 조건을 저장본에 함께 붙입니다. 화면의 결과는 그대로입니다.")
    revs = ([widget._clarifications] if widget._clarifications else []) + list(widget.revisions)
    widget._library_dialog = LibraryDialog(widget, widget.original, saved_result,
                                           widget.task.currentData(), widget.detail.currentData(),
                                           revisions=revs)
    widget._library_dialog.restored.connect(widget._restore_library)
    widget._library_dialog.open()


def build_confirm_dialog(widget, title: str, text: str, callback) -> QMessageBox:
    dialog = QMessageBox(QMessageBox.Icon.Question, title, text,
                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, widget)
    dialog.setDefaultButton(QMessageBox.StandardButton.No)
    dialog.setEscapeButton(QMessageBox.StandardButton.No)
    dialog.finished.connect(lambda value: callback() if value == QMessageBox.StandardButton.Yes else None)
    return dialog
