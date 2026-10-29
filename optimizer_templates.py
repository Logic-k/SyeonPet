"""Template picker for the prompt conversation.

The original catalog comes from prompt-toolkit-desktop desktop/ui/templates.js
at commit b44baedd028988e26fd227113fe8c4855c1e0541. A separate office catalog
is informed by public information from the Seobu District Office of Education:
https://home.pen.go.kr/seobu/cm/cntnts/cntntsView.do?cntntsId=55&mi=10163
https://home.pen.go.kr/seobu/ad/bs/beffatinfosport/view/selectBeffatinfoList.do
These are independent examples, not official forms. Both catalogs are local.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QVBoxLayout)


def _catalog_path(filename: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "assets" / filename


def _read_catalog(filename: str) -> list[dict[str, str]]:
    path = _catalog_path(filename)
    with path.open(encoding="utf-8") as file:
        data = json.load(file)
    required = {"id", "category", "label", "description", "text"}
    if not isinstance(data, list) or not all(
        isinstance(item, dict) and required <= item.keys()
        and all(isinstance(item[key], str) for key in required)
        for item in data
    ):
        raise ValueError("템플릿 데이터 형식이 올바르지 않습니다.")
    return data


def load_templates() -> list[dict[str, str]]:
    original = _read_catalog("prompt_templates.json")
    office = _read_catalog("seobu_office_templates.json")
    combined = original + office
    if len({item["id"] for item in combined}) != len(combined):
        raise ValueError("중복된 템플릿 ID가 있습니다.")
    return combined


def filter_templates(templates: list[dict[str, str]], query: str = "",
                     category: str = "", office_only: bool = False) -> list[dict[str, str]]:
    query = query.strip().casefold()
    return [item for item in templates
            if (not category or item["category"] == category)
            and (not office_only or item["id"].startswith("seobu-"))
            and (not query or query in " ".join(
                item[key] for key in ("label", "description", "category", "text")
            ).casefold())]


class TemplatePicker(QDialog):
    def __init__(self, parent=None, on_selected: Callable[[str], None] | None = None):
        super().__init__(parent)
        self._on_selected = on_selected
        self._drafts: dict[str, str] = {}
        self._current_id: str | None = None
        self._loading = False
        self.setWindowTitle("프롬프트 템플릿")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(540, 560)

        layout = QVBoxLayout(self)
        intro = QLabel("업무에 맞는 예시를 골라 [빈칸]을 채우고 원문을 확인하세요. 기관 공식 서식이 아닌 작성 도우미입니다.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.office_button = QPushButton("교육지원청 업무 예시 보기")
        self.office_button.setCheckable(True)
        self.office_button.setAccessibleName("교육지원청 업무 예시만 보기")
        layout.addWidget(self.office_button)
        privacy = QLabel("외부 AI에는 실제 학생·학부모 개인정보와 비공개 자료를 입력하지 말고, 기관 정책과 결과를 직접 확인하세요.")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        self.catalog_warning = QLabel()
        self.catalog_warning.setWordWrap(True)
        self.catalog_warning.setVisible(False)
        layout.addWidget(self.catalog_warning)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("템플릿 검색")
        self.search.setAccessibleName("템플릿 검색")
        self.category = QComboBox()
        self.category.setAccessibleName("템플릿 분야")
        self.category.addItem("전체 분야", "")
        filters.addWidget(self.search, 1)
        filters.addWidget(self.category)
        layout.addLayout(filters)

        self.list = QListWidget()
        self.list.setAccessibleName("템플릿 목록")
        layout.addWidget(self.list, 1)
        self.description = QLabel("템플릿을 선택해 주세요.")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.preview = QPlainTextEdit()
        self.preview.setAccessibleName("템플릿 내용 편집")
        self.preview.setPlaceholderText("선택한 템플릿의 내용을 여기서 수정할 수 있습니다.")
        layout.addWidget(self.preview, 2)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("취소")
        self.select_button = QPushButton("입력란에 넣기")
        self.select_button.setEnabled(False)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.select_button)
        layout.addLayout(buttons)

        try:
            self._templates = load_templates()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            # An unavailable office catalog must never hide the original data.
            try:
                self._templates = _read_catalog("prompt_templates.json")
            except (OSError, ValueError, json.JSONDecodeError):
                self._templates = []
            self.catalog_warning.setText(f"일부 템플릿을 불러오지 못했습니다: {exc}")
            self.catalog_warning.setVisible(True)
            self.office_button.setEnabled(False)
        for name in dict.fromkeys(item["category"] for item in self._templates):
            self.category.addItem(name, name)
        self.search.textChanged.connect(self._refresh)
        self.category.currentIndexChanged.connect(self._refresh)
        self.office_button.toggled.connect(self._set_office_mode)
        self.list.currentItemChanged.connect(self._show_item)
        self.preview.textChanged.connect(self._save_draft)
        self.cancel_button.clicked.connect(self.reject)
        self.select_button.clicked.connect(self._choose)
        self._refresh()

    def _set_office_mode(self, _enabled):
        # Avoid retaining an incompatible category from the previous view.
        self.category.setCurrentIndex(0)
        self._refresh()

    def _refresh(self, *_):
        previous = self._current_id
        self.list.blockSignals(True)
        self.list.clear()
        selected = None
        for template in filter_templates(self._templates, self.search.text(),
                                         self.category.currentData() or "",
                                         office_only=self.office_button.isChecked()):
            item = QListWidgetItem(f"{template['label']}  ·  {template['category']}")
            item.setData(Qt.ItemDataRole.UserRole, template)
            self.list.addItem(item)
            if template["id"] == previous:
                selected = item
        if selected:
            self.list.setCurrentItem(selected)
        self.list.blockSignals(False)
        self._show_item(selected)

    def _show_item(self, item, *_):
        self._loading = True
        try:
            if item is None:
                self._current_id = None
                self.preview.clear()
                if self._templates:
                    self.description.setText("템플릿을 선택해 주세요.")
                self.select_button.setEnabled(False)
                return
            template = item.data(Qt.ItemDataRole.UserRole)
            self._current_id = template["id"]
            self.description.setText(template["description"])
            self.preview.setPlainText(self._drafts.get(template["id"], template["text"]))
            self.select_button.setEnabled(bool(self.preview.toPlainText().strip()))
        finally:
            self._loading = False

    def _save_draft(self):
        if not self._loading and self._current_id is not None:
            self._drafts[self._current_id] = self.preview.toPlainText()
            self.select_button.setEnabled(bool(self.preview.toPlainText().strip()))

    def _choose(self):
        text = self.preview.toPlainText()
        if not text.strip():
            return
        if self._on_selected is not None:
            self._on_selected(text)
        self.accept()


def show_template_picker(parent=None, on_selected: Callable[[str], None] | None = None) -> TemplatePicker:
    """Open without a nested event loop; invoke on_selected only on acceptance."""
    dialog = TemplatePicker(parent, on_selected)
    dialog.show()
    return dialog
