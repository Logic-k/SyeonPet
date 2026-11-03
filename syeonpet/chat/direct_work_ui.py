"""SyeonPet Novice Direct Work UI Widget.

Novice-first "✨ 바로 도움받기" widget:
Generates complete Korean office deliverables directly from simple descriptions,
preventing the meta-prompt trap, with instant offline templates and safe AI assistance.
"""
from __future__ import annotations

from pathlib import Path
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from syeonpet.privacy.detector import detect_pii, mask_pii, summarize_pii
from syeonpet.work.assistant_core import create_direct_draft, SYSTEM as ASSISTANT_SYSTEM
from syeonpet.work.web_ai_bridge import copy_and_open_web_ai, GUIDE_MESSAGE


CHIPS = [
    ("📱 학부모·안내 문자", "내일 오후 3시까지 방과후학교 신청 마감 안내 문자 작성해 줘"),
    ("📧 업무 협조 메일", "교무부에서 각 학년부장님들께 교육과정 평가회 자료 제출 협조 메일 작성해 줘"),
    ("📄 공문 기안 초안", "2026학년도 1학기 방과후학교 운영 계획 수립 공문 기안 초안 작성해 줘"),
    ("📝 회의록·할일 정리", "아래 회의 내용을 바탕으로 주요 결정사항과 부서별 할 일 정리해 줘:\n\n"),
    ("✅ 행사 체크리스트", "학부모 총회 및 학교설명회 준비 체크리스트 만들어 줘"),
    ("📊 엑셀 수식 질문", "A열이 '완료'이고 B열이 '교무부'인 행의 C열 금액 합계를 구하는 엑셀 수식 알려줘"),
    ("💬 공손한 표현 다듬기", "이번 주까지 제출 안 하시면 처리 불가합니다. 공손하고 정중하게 바꿔 줘"),
]


class _AIWorker(QThread):
    finished_signal = Signal(str, str)  # (result, error)

    def __init__(self, prompt: str):
        super().__init__()
        self.prompt = prompt

    def run(self):
        try:
            import optimizer_ai
            resp = optimizer_ai.optimize_ai(
                self.prompt,
                provider="jarvisclaw",
                task="writing",
                detail="balanced",
                system_prompt=ASSISTANT_SYSTEM,
            )
            text = resp.get("result", "") if isinstance(resp, dict) else str(resp)
            self.finished_signal.emit(text, "")
        except Exception as exc:
            self.finished_signal.emit("", str(exc))


class DirectWorkWidget(QWidget):
    """Novice-first direct deliverable generation widget."""

    busy_changed = Signal(bool)
    task_feedback = Signal(str)  # "success", "failure", "copied"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("directWorkWidget")
        self._busy = False
        self._current_scale = 10.0
        self._worker: _AIWorker | None = None
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._detach_worker)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header
        self.header_label = QLabel(
            "프롬프트 몰라도 괜찮아요! 필요한 업무를 편하게 적어주시면 바로 초안·요약·수식을 만들어 드려요."
        )
        self.header_label.setWordWrap(True)
        self.header_label.setStyleSheet("color: #172b3a; font-weight: 600; padding: 2px;")
        layout.addWidget(self.header_label)

        # Quick starter chips row
        chips_scroll = QScrollArea()
        chips_scroll.setWidgetResizable(True)
        chips_scroll.setFixedHeight(38)
        chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        chips_widget = QWidget()
        chips_layout = QHBoxLayout(chips_widget)
        chips_layout.setContentsMargins(0, 0, 0, 0)
        chips_layout.setSpacing(4)
        for label, template in CHIPS:
            btn = QPushButton(label)
            btn.setStyleSheet("font-size: 11px; padding: 3px 8px; border-radius: 12px; background: #e3effa; color: #154c79;")
            btn.clicked.connect(lambda checked=False, t=template: self._apply_chip(t))
            chips_layout.addWidget(btn)
        chips_layout.addStretch()
        chips_scroll.setWidget(chips_widget)
        layout.addWidget(chips_scroll)

        # PII Warning Banner
        self.pii_banner = QLabel()
        self.pii_banner.setStyleSheet(
            "background: #fff3cd; color: #856404; border: 1px solid #ffeeba; border-radius: 4px; padding: 4px 6px;"
        )
        self.pii_banner.setWordWrap(True)
        self.pii_banner.hide()
        layout.addWidget(self.pii_banner)

        # Input Area with Load File button & Mask PII button
        input_toolbar = QHBoxLayout()
        input_toolbar.addWidget(QLabel("요청 또는 자료:"))
        self.load_file_btn = QPushButton("📂 파일 내용 불러오기")
        self.load_file_btn.clicked.connect(self._open_file_dialog)
        input_toolbar.addWidget(self.load_file_btn)
        self.mask_pii_btn = QPushButton("🛡️ 개인정보 가리기")
        self.mask_pii_btn.clicked.connect(self._mask_pii_action)
        input_toolbar.addWidget(self.mask_pii_btn)
        input_toolbar.addStretch()
        layout.addLayout(input_toolbar)

        self.input = QTextEdit()
        self.input.setPlaceholderText("예: 학부모 총회 안내 문자 작성, 날짜는 3월 25일 오후 2시 강당")
        self.input.setAccessibleName("바로 작성 요청 입력")
        self.input.setMinimumHeight(70)
        self.input.textChanged.connect(self._on_input_changed)
        layout.addWidget(self.input, 2)

        # Execution Mode & Submit Row
        exec_row = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("⚡ 즉시 서식 초안 (외부 전송 없음 · 안전)", "offline_direct")
        self.mode_combo.addItem("🤖 무료 AI로 자연스럽게 작성 (JarvisClaw)", "ai_direct")
        exec_row.addWidget(self.mode_combo, 1)

        self.submit_btn = QPushButton("✨ 바로 작성하기")
        self.submit_btn.setStyleSheet("font-weight: 600; background: #195b83; color: white; padding: 6px 12px; border-radius: 4px;")
        self.submit_btn.clicked.connect(self.submit)
        exec_row.addWidget(self.submit_btn)
        layout.addLayout(exec_row)

        # Output Area
        layout.addWidget(QLabel("완성된 결과:"))
        self.result = QTextEdit()
        self.result.setPlaceholderText("작성하기를 누르면 이곳에 완성된 초안이 표시됩니다.")
        self.result.setAccessibleName("바로 작성 결과")
        self.result.setMinimumHeight(100)
        layout.addWidget(self.result, 3)

        # Action Buttons Row
        action_row = QHBoxLayout()
        self.copy_btn = QPushButton("📋 결과 복사")
        self.copy_btn.clicked.connect(self._copy_result)
        action_row.addWidget(self.copy_btn)

        self.save_btn = QPushButton("💾 문서로 저장")
        self.save_btn.clicked.connect(self._save_to_document)
        action_row.addWidget(self.save_btn)

        self.open_web_btn = QPushButton("🌐 복사 후 뤼튼/ChatGPT 열기")
        self.open_web_btn.clicked.connect(self._open_web_ai)
        action_row.addWidget(self.open_web_btn)
        layout.addLayout(action_row)

    def _apply_chip(self, template: str):
        if self._busy:
            return
        self.input.setPlainText(template)
        self.input.setFocus()

    def _on_input_changed(self):
        text = self.input.toPlainText()
        summary = summarize_pii(text)
        if summary:
            self.pii_banner.setText(f"⚠️ {summary} - '개인정보 가리기'를 눌러 안전하게 변환할 수 있습니다.")
            self.pii_banner.show()
        else:
            self.pii_banner.hide()

    def _mask_pii_action(self):
        text = self.input.toPlainText()
        if not text.strip():
            return
        masked = mask_pii(text)
        self.input.setPlainText(masked)
        self.pii_banner.hide()
        self.task_feedback.emit("success")

    def _open_file_dialog(self):
        if self._busy:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "파일 내용 불러오기",
            "",
            "문서 파일 (*.txt *.hwpx *.docx *.xlsx *.csv *.pdf);;모든 파일 (*.*)",
        )
        if not path:
            return
        try:
            from syeonpet.finder import extract_document_text
            text, badge = extract_document_text(path)
        except Exception as exc:
            QMessageBox.warning(self, "파일 열기 실패", f"파일 내용을 읽지 못했습니다:\n{exc}")
            return
        if not text.strip():
            QMessageBox.information(
                self, "본문 읽기 불가",
                badge or "이 문서에서는 텍스트를 추출할 수 없어요. 파일 이름을 참고해 요청을 적어 주세요.")
            return
        self.load_from_file_finder(Path(path).name, text, badge)

    def load_from_file_finder(self, file_name: str, extracted_text: str, badge: str = ""):
        """Populate input from file finder search results."""
        clean_text = extracted_text.strip()
        self.input.setPlainText(f"아래 문서({file_name}) 내용을 핵심 위주로 요약하고 할 일을 정리해 주세요:\n\n{clean_text}")
        self._on_input_changed()
        self.input.setFocus()

    load_document = load_from_file_finder

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.submit_btn.setEnabled(not busy)
        self.input.setReadOnly(busy)
        self.mode_combo.setEnabled(not busy)
        self.busy_changed.emit(busy)

    def submit(self):
        """Execute deliverable drafting based on selected execution mode."""
        if self._busy:
            return
        prompt = self.input.toPlainText().strip()
        if not prompt:
            QMessageBox.information(self, "입력 확인", "먼저 필요한 업무나 요청을 입력해 주세요.")
            return

        mode = self.mode_combo.currentData()
        if mode == "offline_direct":
            draft = create_direct_draft(prompt)
            self.result.setPlainText(draft["result"])
            self.task_feedback.emit("success")
            return

        # AI direct mode
        summary = summarize_pii(prompt)
        pii_warning = f"\n\n주의: {summary}가 감지되었습니다. 먼저 가리기를 권장합니다." if summary else ""
        box = QMessageBox(
            QMessageBox.Icon.Question,
            "무료 AI 전송 확인",
            "무료 AI(JarvisClaw)를 통해 완성된 초안을 작성합니다. "
            "요청 내용이 외부 모델 제공자에게 전송됩니다." + pii_warning + "\n\n계속 진행할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self,
        )
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        self._worker = _AIWorker(prompt)
        self._worker.finished_signal.connect(self._on_ai_finished)
        self._worker.start()

    def _detach_worker(self):
        """Release a running AI worker so it is never destroyed mid-request."""
        worker = self._worker
        if worker is not None and worker.isRunning():
            try:
                worker.finished_signal.disconnect(self._on_ai_finished)
            except (TypeError, RuntimeError):
                pass
            worker.setParent(None)
            worker.finished.connect(worker.deleteLater)
        self._worker = None

    def closeEvent(self, event):
        self._detach_worker()
        super().closeEvent(event)

    def _on_ai_finished(self, result: str, error: str):
        self._set_busy(False)
        if result and not error:
            self.result.setPlainText(result)
            self.task_feedback.emit("success")
        else:
            # Automatic graceful fallback to offline deliverable
            fallback = create_direct_draft(self.input.toPlainText())
            self.result.setPlainText(fallback["result"])
            QMessageBox.information(
                self,
                "오프라인 초안 전환 안내",
                "외부 AI 연결이 원활하지 않아 오프라인 표준 서식 초안으로 즉시 작성했습니다.",
            )
            self.task_feedback.emit("success")

    def _copy_result(self):
        text = self.result.toPlainText()
        if not text:
            return
        app = QApplication.instance()
        if app and app.clipboard():
            app.clipboard().setText(text)
            self.task_feedback.emit("copied")

    def _save_to_document(self):
        text = self.result.toPlainText()
        if not text:
            return
        path, filt = QFileDialog.getSaveFileName(
            self,
            "문서로 저장",
            "초안.hwpx",
            "한글 문서 (*.hwpx);;텍스트 문서 (*.txt);;마크다운 문서 (*.md);;엑셀 CSV (*.csv);;한글 붙여넣기용 HTML (*.html)",
        )
        if not path:
            return
        try:
            from syeonpet.work.doc_export import export_deliverable
            export_deliverable(text, path)
            self.task_feedback.emit("success")
            QMessageBox.information(self, "저장 완료", f"파일을 저장했습니다:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "저장 실패", f"파일을 저장하지 못했습니다:\n{exc}")

    def _open_web_ai(self):
        text = self.result.toPlainText() or self.input.toPlainText()
        copy_and_open_web_ai(text, "wrtn")
        self.task_feedback.emit("copied")
        QMessageBox.information(self, "웹 AI 연결", GUIDE_MESSAGE)

    def set_font_scale(self, pt: float):
        """Scale font sizes throughout the widget for readability."""
        self._current_scale = pt
        font = self.font()
        font.setPointSizeF(pt)
        for w in (self.input, self.result, self.header_label, self.pii_banner):
            w.setFont(font)
