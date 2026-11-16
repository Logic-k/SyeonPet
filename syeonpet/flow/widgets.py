"""Reusable sub-widgets for the guided prompt flow dialog."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from syeonpet.chat.dialogs import _ActionRow, sync_topmost_parent
from .assembler import apply_slot_fills, extract_fillable_slots, inspect_prompt_quality
from .preferences import PreferenceStore

INPUT_STARTERS: tuple[tuple[str, str], ...] = (
    ("📊 주간 업무 보고", "이번 주에 한 일을 정리해서 과장님께 낼 부서 주간보고 써줘"),
    ("📱 학부모 안내문", "우리 학교 방과후학교 프로그램 안내 가정통신문 만들어줘"),
    ("📝 교무회의 회의록", "오늘 교무회의 내용을 회의록으로 정리해줘"),
    ("💬 학부모 상담 회신", "학부모가 상담을 신청했는데 정중한 답변 문자 써줘"),
    ("📑 공문 답변 초안", "교육청에서 온 공문에 우리 학교 답변 초안 써줘"),
    ("📅 사업·예산 계획", "내년도 교육지원 사업 추진 계획서 초안 작성해줘"),
    ("📚 수업·활동 지도안", "초등 5학년 국어 수업 학습 활동 지도안 만들어줘"),
)

REFINE_PRESETS: tuple[tuple[str, str], ...] = (
    ("짧게 줄이기", "핵심만 남기고 전체 분량을 더 간결하게 줄여 주세요."),
    ("공문체로 다듬기", "교육기관 공문서 문체에 맞게 객관적이고 격식 있게 다듬어 주세요."),
    ("표 형식으로", "비교·일정·역할 항목을 읽기 쉬운 표 형식으로 구조화해 주세요."),
    ("확인 표시 보완", "빠지거나 불명확한 조건을 [확인 필요]로 더 분명히 구분해 주세요."),
)


class ProfileCardDialog(QDialog):
    """Modal editor for locally saved persona/organization context cards."""

    def __init__(self, parent: QWidget | None = None, store: PreferenceStore | None = None):
        super().__init__(parent)
        self._store = store or PreferenceStore()
        self._cards: list[dict] = []
        self._current_id: str | None = None
        self.setObjectName("flowDialog")
        self.setWindowTitle("자주 쓰는 내 정보 카드 관리")
        self.resize(500, 380)

        root = QVBoxLayout(self)
        intro = QLabel(
            "소속 학교·부서, 기본 보고 대상, 서명 등을 카드로 저장해 두면 "
            "질문 화면에서 한 번 눌러 바로 채울 수 있어요. (이 기기에만 저장됩니다)"
        )
        intro.setWordWrap(True)
        intro.setObjectName("flowMuted")
        root.addWidget(intro)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self.card_list = QListWidget()
        self.card_list.setAccessibleName("저장된 내 정보 카드 목록")
        self.card_list.currentRowChanged.connect(self._on_select)
        left.addWidget(self.card_list, 1)

        list_btns = QHBoxLayout()
        self.new_card_btn = QPushButton("새 카드")
        self.new_card_btn.clicked.connect(self._new_card)
        self.del_card_btn = QPushButton("삭제")
        self.del_card_btn.clicked.connect(self._delete_card)
        list_btns.addWidget(self.new_card_btn)
        list_btns.addWidget(self.del_card_btn)
        left.addLayout(list_btns)
        body.addLayout(left, 2)

        right = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("예: ○○초 교무부")
        self.name_edit.setAccessibleName("카드 이름")
        self.org_edit = QLineEdit()
        self.org_edit.setPlaceholderText("예: △△초등학교 교무실")
        self.org_edit.setAccessibleName("소속·학교 또는 부서")
        self.audience_edit = QLineEdit()
        self.audience_edit.setPlaceholderText("예: 교감·교장 선생님 또는 학부모")
        self.audience_edit.setAccessibleName("기본 보고·수신 대상")
        self.sender_edit = QLineEdit()
        self.sender_edit.setPlaceholderText("예: 담당 교사 또는 김○○")
        self.sender_edit.setAccessibleName("기본 발신인·서명")
        self.tone_edit = QLineEdit()
        self.tone_edit.setPlaceholderText("예: 정중하고 간결한 안내체")
        self.tone_edit.setAccessibleName("선호 문체·톤")
        right.addRow("카드 이름*", self.name_edit)
        right.addRow("소속·학교", self.org_edit)
        right.addRow("기본 대상", self.audience_edit)
        right.addRow("발신·서명", self.sender_edit)
        right.addRow("선호 문체", self.tone_edit)
        body.addLayout(right, 3)
        root.addLayout(body, 1)

        footer = QHBoxLayout()
        self.status_lbl = QLabel()
        self.status_lbl.setObjectName("flowMuted")
        footer.addWidget(self.status_lbl, 1)
        self.save_card_btn = QPushButton("카드 저장")
        self.save_card_btn.setObjectName("flowPrimary")
        self.save_card_btn.clicked.connect(self._save_card)
        self.close_btn = QPushButton("닫기")
        self.close_btn.clicked.connect(self.accept)
        footer.addWidget(self.save_card_btn)
        footer.addWidget(self.close_btn)
        root.addLayout(footer)

        sync_topmost_parent(self, parent)
        self._reload()

    def _reload(self, select_id: str | None = None) -> None:
        try:
            self._cards = self._store.load_profile_cards()
        except OSError:
            self._cards = []
        self.card_list.clear()
        target_row = -1
        for i, card in enumerate(self._cards):
            self.card_list.addItem(card["name"])
            if select_id and card["id"] == select_id:
                target_row = i
        if target_row >= 0:
            self.card_list.setCurrentRow(target_row)
        elif self._cards:
            self.card_list.setCurrentRow(0)
        else:
            self._new_card()

    def _on_select(self, row: int) -> None:
        if 0 <= row < len(self._cards):
            card = self._cards[row]
            self._current_id = card["id"]
            self.name_edit.setText(card["name"])
            self.org_edit.setText(card["org"])
            self.audience_edit.setText(card["audience"])
            self.sender_edit.setText(card["sender"])
            self.tone_edit.setText(card["tone"])
            self.del_card_btn.setEnabled(True)
        else:
            self.del_card_btn.setEnabled(False)

    def _new_card(self) -> None:
        self._current_id = None
        self.card_list.clearSelection()
        for edit in (self.name_edit, self.org_edit, self.audience_edit, self.sender_edit, self.tone_edit):
            edit.clear()
        self.del_card_btn.setEnabled(False)
        self.name_edit.setFocus()

    def _save_card(self) -> None:
        payload = {
            "id": self._current_id or "",
            "name": self.name_edit.text(),
            "org": self.org_edit.text(),
            "audience": self.audience_edit.text(),
            "sender": self.sender_edit.text(),
            "tone": self.tone_edit.text(),
        }
        try:
            saved = self._store.save_profile_card(payload)
        except (ValueError, OSError) as exc:
            msg_box = QMessageBox(QMessageBox.Icon.Warning, "카드 저장 확인", str(exc),
                                QMessageBox.StandardButton.Ok, self)
            sync_topmost_parent(msg_box, self)
            msg_box.open()
            return
        self.status_lbl.setText(f"'{saved['name']}' 카드를 저장했어요.")
        self._reload(select_id=saved["id"])

    def _delete_card(self) -> None:
        if not self._current_id:
            return
        try:
            self._store.delete_profile_card(self._current_id)
        except OSError:
            return
        self.status_lbl.setText("선택한 카드를 지웠어요.")
        self._reload()


class SlotFillBar(QFrame):
    """Interactive inline editor for unresolved ``[확인 필요]`` / ``[대괄호]`` slots."""

    applied = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("flowSubPanel")
        self._source_text = ""
        self._editors: list[tuple[dict, QLineEdit]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        head = QHBoxLayout()
        self.title_lbl = QLabel()
        self.title_lbl.setObjectName("flowSection")
        head.addWidget(self.title_lbl, 1)
        self.apply_btn = QPushButton("✨ 빈칸 반영하기")
        self.apply_btn.clicked.connect(self._on_apply)
        head.addWidget(self.apply_btn)
        outer.addLayout(head)

        self.form_holder = QWidget()
        self.form_layout = QFormLayout(self.form_holder)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout.setSpacing(4)
        outer.addWidget(self.form_holder)
        self.hide()

    def refresh_slots(self, text: str) -> None:
        self._source_text = text
        slots = extract_fillable_slots(text)
        while self.form_layout.rowCount():
            self.form_layout.removeRow(0)
        self._editors = []
        if not slots:
            self.hide()
            return
        shown = slots[:6]
        extra = len(slots) - len(shown)
        suffix = f" (+{extra}개는 아래 편집란에서 직접 수정)" if extra > 0 else ""
        self.title_lbl.setText(f"아직 비어 있는 칸 {len(slots)}개 — 여기서 바로 채울 수 있어요{suffix}")
        for slot in shown:
            edit = QLineEdit()
            edit.setPlaceholderText(f"{slot['label']} 입력 (예: 내용 또는 '없음')")
            edit.setAccessibleName(f"빈칸 채우기: {slot['label']}")
            edit.returnPressed.connect(self._on_apply)
            self.form_layout.addRow(slot["label"], edit)
            self._editors.append((slot, edit))
        self.show()

    def _on_apply(self) -> None:
        fills = [(slot, edit.text()) for slot, edit in self._editors if edit.text().strip()]
        if not fills:
            return
        updated = apply_slot_fills(self._source_text, fills)
        if updated != self._source_text:
            self.applied.emit(updated)


class PromptQualityBar(QFrame):
    """Non-blocking literal condition preservation bar for the result screen."""

    supplement_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("flowSubPanel")
        self._missing_literals: list[str] = []

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(6)
        self.status_lbl = QLabel()
        self.status_lbl.setWordWrap(True)
        row.addWidget(self.status_lbl, 1)
        self.fix_btn = QPushButton("누락 조건 보충")
        self.fix_btn.clicked.connect(self._emit_fix)
        self.fix_btn.hide()
        row.addWidget(self.fix_btn)
        self.hide()

    def update_report(self, original: str, context: str, prompt_text: str) -> None:
        report = inspect_prompt_quality(original, context, prompt_text)
        total = report["total"]
        missing = report["missing"]
        self._missing_literals = list(dict.fromkeys(item["literal"] for item in missing))
        if total == 0:
            self.hide()
            return
        if not self._missing_literals:
            self.status_lbl.setText(f"✓ 원문·답변의 핵심 조건 {total}건이 프롬프트에 모두 포함됐어요.")
            self.status_lbl.setStyleSheet("color: #2f6b3a; font-weight: 600;")
            self.fix_btn.hide()
            self.show()
            return
        preview = ", ".join(self._missing_literals[:4])
        if len(self._missing_literals) > 4:
            preview += f" 외 {len(self._missing_literals) - 4}건"
        self.status_lbl.setText(f"⚠️ 원문 조건 중 빠진 항목: {preview}")
        self.status_lbl.setStyleSheet("color: #8a5300; font-weight: 600;")
        self.fix_btn.show()
        self.show()

    def _emit_fix(self) -> None:
        if self._missing_literals:
            lines = "\n".join(f"- 반영할 조건: {lit}" for lit in self._missing_literals)
            self.supplement_requested.emit(lines)


def build_chip_button(text: str, *, checkable: bool = False) -> QPushButton:
    """Create a uniform secondary chip button used across flow panels."""
    btn = QPushButton(text)
    btn.setObjectName("flowChip")
    btn.setCheckable(checkable)
    return btn
