"""문서 도구 섹션 — 공무원용 한글 문서 작업 모음.

모든 도구는 로컬에서만 동작하고 원본을 건드리지 않는다.
카드 = 도구 하나. 파일/폴더를 고르고 실행하면 상태 칸에 결과가 나온다.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QVBoxLayout, QWidget, QMessageBox,
)

from syeonpet.chat.dialogs import _ActionRow
from syeonpet.docs import batch as _batch
from syeonpet.docs import compare as _compare
from syeonpet.docs import form_fill as _form_fill
from syeonpet.docs import official_form as _form
from syeonpet.docs import official_rules as _rules

_DOC_FILTER = "문서 (*.txt *.md *.hwp *.hwpx *.docx *.xlsx *.csv *.pptx *.pdf)"


def _card(title: str, desc: str) -> tuple[QFrame, QVBoxLayout]:
    box = QFrame()
    box.setObjectName("docsToolCard")
    box.setStyleSheet(
        "QFrame#docsToolCard { background: #ffffff; border: 1px solid #dbe4ee; "
        "border-radius: 8px; }")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(10, 8, 10, 10)
    lay.setSpacing(6)
    t = QLabel(title)
    t.setStyleSheet("font-weight: 600; color: #172b3a;")
    lay.addWidget(t)
    d = QLabel(desc)
    d.setWordWrap(True)
    d.setStyleSheet("color: #5a6b7a; font-size: 12px;")
    lay.addWidget(d)
    return box, lay


class DocsToolsWidget(QWidget):
    """Novice-friendly 문서 도구 section — one card per tool."""

    task_feedback = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("docsToolsWidget")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        head = QLabel("문서 작업은 전부 내 PC 안에서만 처리되며, 원본 파일은 바뀌지 않아요.")
        head.setWordWrap(True)
        head.setStyleSheet("color: #172b3a; font-weight: 600; padding: 2px;")
        root.addWidget(head)

        root.addWidget(self._build_review_card())
        root.addWidget(self._build_form_card())
        root.addWidget(self._build_compare_card())
        root.addWidget(self._build_batch_card())
        root.addWidget(self._build_fill_card())
        root.addStretch(1)

        self.status = QLabel("도구를 골라 파일이나 폴더를 넣어 주세요.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #2563eb; padding: 4px;")
        root.addWidget(self.status)

    # ---------- 공문서 검수 ----------
    def _build_review_card(self) -> QFrame:
        box, lay = _card("🧾 공문서 표기 검수",
                         "날짜·시간·금액·전화번호·붙임 표기가 규칙에 맞는지 줄마다 알려 드려요.")
        row = _ActionRow()
        btn = QPushButton("문서 골라 검수하기")
        btn.clicked.connect(self._on_review)
        row.addWidget(btn)
        self.review_out_btn = QPushButton("결과를 문서로 저장")
        self.review_out_btn.clicked.connect(lambda: self._save_text(self._review_text, "검수결과"))
        self.review_out_btn.setEnabled(False)
        row.addWidget(self.review_out_btn)
        lay.addLayout(row)
        self._review_text = ""
        return box

    def _on_review(self):
        path, _ = QFileDialog.getOpenFileName(self, "검수할 문서 선택", "", _DOC_FILTER)
        if not path:
            return
        from syeonpet.finder.extractors import extract_document_text
        text, badge = extract_document_text(path)
        if not text.strip():
            self.status.setText("이 문서에서는 본문을 읽지 못했어요." + (f" ({badge})" if badge else ""))
            return
        findings = _rules.review_official_document(text)
        self._review_text = _rules.render_report(text, findings)
        self.review_out_btn.setEnabled(True)
        self.status.setText(_rules.summarize_findings(findings))
        self.task_feedback.emit("success")

    # ---------- 초안 → 공문서 ----------
    def _build_form_card(self) -> QFrame:
        box, lay = _card("📄 초안을 공문서 양식으로",
                         "초안을 붙여 넣고 칸을 채우면 기안문·보고서 모양의 한글 문서로 만들어 드려요.")
        self.form_kind = QComboBox()
        for key, label in _form.FORM_LABELS.items():
            self.form_kind.addItem(label, key)
        lay.addWidget(self.form_kind)
        self.form_fields = QTextEdit()
        self.form_fields.setPlaceholderText(
            "한 줄에 한 칸씩 '칸이름: 내용'으로 적어 주세요.\n"
            "예) 제목: 2026년 봄 학부모 연수 계획\n"
            "    기안부서: 교무부    수신: 교장선생님")
        self.form_fields.setMinimumHeight(64)
        self.form_fields.setMaximumHeight(110)
        lay.addWidget(self.form_fields)
        self.form_body = QTextEdit()
        self.form_body.setPlaceholderText("본문 초안을 붙여 넣어 주세요.")
        self.form_body.setMinimumHeight(80)
        lay.addWidget(self.form_body, 1)
        btn = QPushButton("공문서로 저장하기 (.hwpx)")
        btn.clicked.connect(self._on_form_save)
        lay.addWidget(btn)
        return box

    def _on_form_save(self):
        fields: dict[str, str] = {}
        for ln in self.form_fields.toPlainText().splitlines():
            if ":" in ln:
                k, v = ln.split(":", 1)
                fields[k.strip()] = v.strip()
        fields["body"] = self.form_body.toPlainText().strip()
        if not fields["body"] and not fields.get("제목"):
            self.status.setText("본문이나 칸 내용을 먼저 적어 주세요.")
            return
        keymap = {"문서번호": "doc_no", "시행일자": "date", "날짜": "date", "기안부서": "dept",
                  "부서": "dept", "기안자": "drafter", "수신": "recipient", "참조": "cc",
                  "제목": "title", "붙임": "attach"}
        mapped = {keymap.get(k, k): v for k, v in fields.items()}
        mapped["body"] = fields["body"]
        out, _ = QFileDialog.getSaveFileName(
            self, "공문서 저장", "공문서.hwpx", "한글 문서 (*.hwpx);;텍스트 (*.txt);;마크다운 (*.md)")
        if not out:
            return
        try:
            path = _form.export_official_doc(self.form_kind.currentData(), mapped, out)
            self.status.setText(f"저장했어요: {path.name}")
            self.task_feedback.emit("success")
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"저장하지 못했어요: {exc}")

    # ---------- 두 문서 비교 ----------
    def _build_compare_card(self) -> QFrame:
        box, lay = _card("🔍 두 문서 비교 (신구대조표)",
                         "이전 문서와 새 문서의 달라진 곳을 나란히 표로 만들어 드려요.")
        row = _ActionRow()
        old_btn = QPushButton("① 이전 문서 고르기")
        old_btn.clicked.connect(self._pick_old)
        row.addWidget(old_btn)
        new_btn = QPushButton("② 새 문서 고르기")
        new_btn.clicked.connect(self._pick_new)
        row.addWidget(new_btn)
        run_btn = QPushButton("③ 비교하기")
        run_btn.clicked.connect(self._on_compare)
        row.addWidget(run_btn)
        lay.addLayout(row)
        self._cmp_paths = {"old": None, "new": None}
        self.cmp_label = QLabel("이전·새 문서를 차례로 골라 주세요.")
        self.cmp_label.setWordWrap(True)
        self.cmp_label.setStyleSheet("color: #5a6b7a; font-size: 12px;")
        lay.addWidget(self.cmp_label)
        self._cmp_rows: list = []
        self.cmp_save_btn = QPushButton("대조표 저장 (CSV · HWPX)")
        self.cmp_save_btn.setEnabled(False)
        self.cmp_save_btn.clicked.connect(self._on_compare_save)
        lay.addWidget(self.cmp_save_btn)
        return box

    def _pick_old(self):
        path, _ = QFileDialog.getOpenFileName(self, "이전 문서 선택", "", _DOC_FILTER)
        if path:
            self._cmp_paths["old"] = path
            self._update_cmp_label()

    def _pick_new(self):
        path, _ = QFileDialog.getOpenFileName(self, "새 문서 선택", "", _DOC_FILTER)
        if path:
            self._cmp_paths["new"] = path
            self._update_cmp_label()

    def _update_cmp_label(self):
        old = Path(self._cmp_paths["old"]).name if self._cmp_paths["old"] else "?"
        new = Path(self._cmp_paths["new"]).name if self._cmp_paths["new"] else "?"
        self.cmp_label.setText(f"이전: {old}  →  새: {new}")

    def _on_compare(self):
        from syeonpet.finder.extractors import extract_document_text
        if not self._cmp_paths["old"] or not self._cmp_paths["new"]:
            self.status.setText("이전 문서와 새 문서를 둘 다 골라 주세요.")
            return
        old_text, _b1 = extract_document_text(self._cmp_paths["old"])
        new_text, _b2 = extract_document_text(self._cmp_paths["new"])
        if not old_text.strip() or not new_text.strip():
            self.status.setText("한쪽 문서의 본문을 읽지 못했어요.")
            return
        self._cmp_rows = _compare.compare_documents(old_text, new_text)
        changed = _compare.changed_count(self._cmp_rows)
        self.status.setText(f"변경 {changed}곳을 찾았어요 (전체 {len(self._cmp_rows)}행).")
        self.cmp_save_btn.setEnabled(True)
        self.task_feedback.emit("success")

    def _on_compare_save(self):
        out, _ = QFileDialog.getSaveFileName(
            self, "신구대조표 저장", "신구대조표.csv",
            "엑셀 CSV (*.csv);;한글 문서 (*.hwpx)")
        if not out:
            return
        path = Path(out)
        try:
            if path.suffix.lower() == ".hwpx":
                _compare.export_compare_hwpx(self._cmp_rows, path)
            else:
                _compare.export_compare_csv(self._cmp_rows, path)
            self.status.setText(f"저장했어요: {path.name}")
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"저장하지 못했어요: {exc}")

    # ---------- 폴더째 변환 + PII 가림 + hwp→hwpx ----------
    def _build_batch_card(self) -> QFrame:
        box, lay = _card("📂 폴더째 변환",
                         "폴더 안 문서를 한꺼번에 AI용 텍스트로 바꾸거나, 개인정보 가림본·hwpx로 만들어 드려요.")
        self.batch_mode = QComboBox()
        self.batch_mode.addItem("AI용 텍스트로 변환 (.md)", "ai")
        self.batch_mode.addItem("개인정보 가림본 만들기 (.md)", "mask")
        self.batch_mode.addItem("hwp → hwpx로 변환", "hwpx")
        lay.addWidget(self.batch_mode)
        row = _ActionRow()
        btn = QPushButton("폴더 골라 변환 시작")
        btn.clicked.connect(self._on_batch)
        row.addWidget(btn)
        lay.addLayout(row)
        return box

    def _on_batch(self):
        folder = QFileDialog.getExistingDirectory(self, "변환할 문서가 있는 폴더 선택")
        if not folder:
            return
        mode = self.batch_mode.currentData()
        files = _batch.collect_documents(
            folder,
            {".hwp"} if mode == "hwpx" else None)
        if not files:
            self.status.setText("그 폴더에서 읽을 문서를 찾지 못했어요.")
            return
        out_dir = Path(folder) / "_변환결과"
        if mode == "hwpx":
            result = _batch.convert_hwp_to_hwpx(files, out_dir)
        else:
            result = _batch.convert_for_ai(files, out_dir, mask=(mode == "mask"))
        self.status.setText(f"{result.summary} → {out_dir}")
        for src, why in result.failed[:5]:
            self.status.setText(self.status.text() + f"\n  실패: {src.name} — {why}")
        self.task_feedback.emit("success" if result.done else "failure")

    # ---------- 양식 채우기 ----------
    def _build_fill_card(self) -> QFrame:
        box, lay = _card("📋 양식 채우기 (여러 문서 한 번에)",
                         "{{이름}}처럼 빈칸을 둔 양식에 표 한 줄씩 값을 넣어 문서를 여러 장 만들어요.")
        row1 = _ActionRow()
        tmpl_btn = QPushButton("① 양식 문서 고르기")
        tmpl_btn.clicked.connect(self._pick_template)
        row1.addWidget(tmpl_btn)
        data_btn = QPushButton("② 표 데이터 고르기 (.csv/.xlsx)")
        data_btn.clicked.connect(self._pick_rows)
        row1.addWidget(data_btn)
        lay.addLayout(row1)
        self.fill_label = QLabel("양식과 표를 차례로 골라 주세요.")
        self.fill_label.setWordWrap(True)
        self.fill_label.setStyleSheet("color: #5a6b7a; font-size: 12px;")
        lay.addWidget(self.fill_label)
        row2 = _ActionRow()
        run_btn = QPushButton("③ 문서 만들기")
        run_btn.clicked.connect(self._on_fill)
        row2.addWidget(run_btn)
        lay.addLayout(row2)
        self._fill_paths = {"tmpl": None, "rows": None}
        return box

    def _pick_template(self):
        path, _ = QFileDialog.getOpenFileName(self, "양식 문서 선택", "", _DOC_FILTER)
        if path:
            self._fill_paths["tmpl"] = path
            self._update_fill_label()

    def _pick_rows(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "표 데이터 선택", "", "표 (*.csv *.xlsx *.xlsm)")
        if path:
            self._fill_paths["rows"] = path
            self._update_fill_label()

    def _update_fill_label(self):
        t = Path(self._fill_paths["tmpl"]).name if self._fill_paths["tmpl"] else "?"
        r = Path(self._fill_paths["rows"]).name if self._fill_paths["rows"] else "?"
        self.fill_label.setText(f"양식: {t}  +  표: {r}")

    def _on_fill(self):
        from syeonpet.finder.extractors import extract_document_text
        if not self._fill_paths["tmpl"] or not self._fill_paths["rows"]:
            self.status.setText("양식과 표 데이터를 둘 다 골라 주세요.")
            return
        template, badge = extract_document_text(self._fill_paths["tmpl"], max_chars=60000)
        if not template.strip():
            self.status.setText("양식 본문을 읽지 못했어요." + (f" ({badge})" if badge else ""))
            return
        keys = _form_fill.template_keys(template)
        if not keys:
            self.status.setText("양식에서 {{칸}} 표시를 찾지 못했어요. 예: {{성명}} {{날짜}}")
            return
        try:
            rows = _form_fill.read_rows(self._fill_paths["rows"])
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"표 데이터를 읽지 못했어요: {exc}")
            return
        if not rows:
            self.status.setText("표에서 읽을 행이 없어요.")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "만든 문서를 담을 폴더 선택")
        if not out_dir:
            return
        result = _form_fill.fill_documents(template, rows, Path(out_dir))
        self.status.setText(f"{result.summary} → {out_dir} (칸: {', '.join(keys)})")
        for i, why in result.failed[:5]:
            self.status.setText(self.status.text() + f"\n  {i}행 실패: {why}")
        self.task_feedback.emit("success" if result.done else "failure")

    # ---------- 공통 ----------
    def _save_text(self, text: str, stem: str):
        if not text:
            return
        out, _ = QFileDialog.getSaveFileName(
            self, "저장", f"{stem}.txt", "텍스트 (*.txt);;마크다운 (*.md)")
        if out:
            Path(out).write_text(text, encoding="utf-8")
            self.status.setText(f"저장했어요: {Path(out).name}")
