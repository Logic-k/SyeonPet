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

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QBoxLayout, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)


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


class _TemplateContent(QWidget):
    def sizeHint(self):
        # Text editors have a large default size hint. Let the scroll area
        # distribute the available space instead of scrolling at normal size.
        return self.minimumSizeHint()

    def heightForWidth(self, width):
        return self.layout().minimumHeightForWidth(width)


class TemplatePicker(QDialog):
    def __init__(self, parent=None, on_selected: Callable[[str], None] | None = None):
        super().__init__(parent)
        self._on_selected = on_selected
        self._drafts: dict[str, str] = {}
        self._current_id: str | None = None
        self._loading = False
        self.setWindowTitle("프롬프트 템플릿")
        self.setObjectName("templatePicker")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(540, 560)
        self.setStyleSheet("""
            QDialog#templatePicker { background: #f3f6fa; color: #172b3a; }
            QScrollArea#templateScroll, QWidget#templateContent { background: #f3f6fa; border: none; }
            QDialog#templatePicker QLabel { color: #172b3a; }
            QLabel#templateTitle { font-size: 12pt; font-weight: 600; }
            QLabel#templateSection { font-weight: 600; }
            QLabel#templatePrivacy { color: #513f20; }
            QLineEdit, QComboBox, QListWidget, QPlainTextEdit {
                background: #ffffff; color: #172b3a;
                border: 1px solid #9aaebc; border-radius: 4px; padding: 5px;
                selection-background-color: #195b83; selection-color: #ffffff;
            }
            QLineEdit:focus, QComboBox:focus, QListWidget:focus, QPlainTextEdit:focus {
                border: 2px solid #195b83;
            }
            QListWidget::item { padding: 4px; }
            QListWidget::item:selected { background: #195b83; color: #ffffff; }
            QScrollBar:vertical { background: #dce5eb; width: 10px; margin: 0; }
            QScrollBar::handle:vertical { background: #667e8d; min-height: 24px;
                border-radius: 5px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal { background: #dce5eb; height: 10px; margin: 0; }
            QScrollBar::handle:horizontal { background: #667e8d; min-width: 24px;
                border-radius: 5px; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            QPushButton { background: #ffffff; color: #172b3a;
                border: 1px solid #9aaebc; border-radius: 4px;
                padding: 6px 10px; }
            QPushButton:hover { background: #e4edf3; }
            QPushButton:focus { border: 2px solid #195b83; }
            QPushButton:checked { background: #d9eaf4; border-color: #195b83; }
            QPushButton#templateSelect { background: #195b83; color: #ffffff;
                border-color: #195b83; font-weight: 600; }
            QPushButton#templateSelect:hover { background: #124969; }
            QPushButton#templateSelect:disabled { background: #e1e7eb;
                color: #536773; border-color: #aab9c2; }
        """)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(12, 12, 12, 12)
        outer_layout.setSpacing(6)
        scroll = QScrollArea()
        scroll.setObjectName("templateScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = _TemplateContent()
        content.setObjectName("templateContent")
        content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        outer_layout.addWidget(scroll, 1)
        title = QLabel("템플릿 선택")
        title.setObjectName("templateTitle")
        layout.addWidget(title)
        intro = QLabel("예시를 선택하고 [빈칸]을 채워 주세요. 기관 공식 서식은 아닙니다.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.office_button = QPushButton("교육지원청 업무 예시 보기")
        self.office_button.setCheckable(True)
        self.office_button.setAccessibleName("교육지원청 업무 예시만 보기")
        layout.addWidget(self.office_button)
        privacy = QLabel("외부 AI에 개인정보·비공개 자료를 입력하지 말고 기관 정책과 결과를 확인하세요.")
        privacy.setObjectName("templatePrivacy")
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
        search_label = QLabel("검색")
        search_label.setBuddy(self.search)
        filters.addWidget(search_label)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.category)
        layout.addLayout(filters)

        list_label = QLabel("예시 목록")
        list_label.setObjectName("templateSection")
        self.list = QListWidget()
        self.list.setAccessibleName("템플릿 목록")
        self.list.setMinimumHeight(75)
        list_label.setBuddy(self.list)
        layout.addWidget(list_label)
        layout.addWidget(self.list, 3)
        self.description = QLabel("템플릿을 선택해 주세요.")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        preview_label = QLabel("내용 확인 및 수정")
        preview_label.setObjectName("templateSection")
        self.preview = QPlainTextEdit()
        self.preview.setAccessibleName("템플릿 내용 편집")
        self.preview.setPlaceholderText("선택한 템플릿의 내용을 여기서 수정할 수 있습니다.")
        self.preview.setMinimumHeight(110)
        preview_label.setBuddy(self.preview)
        layout.addWidget(preview_label)
        layout.addWidget(self.preview, 4)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("취소")
        self.select_button = QPushButton("입력란에 넣기")
        self.select_button.setObjectName("templateSelect")
        self.select_button.setEnabled(False)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.select_button)
        self._buttons_layout = buttons
        outer_layout.addLayout(buttons)
        scroll.setWidget(content)

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
        self.search.setFocus()

    def _available_geometry(self):
        # A dialog belongs on the monitor containing its parent, which need not
        # be the primary display (and may have negative desktop coordinates).
        parent = self.parentWidget()
        screen = parent.screen() if parent is not None else self.screen()
        return screen.availableGeometry() if screen is not None else None

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_to_screen()
        # Some window managers only report the final native frame after show.
        QTimer.singleShot(0, self, self._fit_to_screen)

    def _fit_to_screen(self):
        bounds = self._available_geometry()
        if bounds is None or bounds.isEmpty():
            return
        frame = self.frameGeometry()
        # The non-client title bar and border also need to fit inside the work
        # area; the editor scroll area handles the reduced client space.
        extra_width = max(0, frame.width() - self.width())
        extra_height = max(0, frame.height() - self.height())
        # Leave a few pixels for native frame rounding and display scaling.
        margin = 4
        margins = self.layout().contentsMargins()
        button_width = (self.cancel_button.sizeHint().width() +
                        self.select_button.sizeHint().width() +
                        self._buttons_layout.spacing() +
                        margins.left() + margins.right())
        available_width = bounds.width() - extra_width - 2 * margin
        direction = (QBoxLayout.Direction.TopToBottom if button_width > available_width
                     else QBoxLayout.Direction.LeftToRight)
        self._buttons_layout.setDirection(direction)
        self.layout().activate()
        self.resize(min(self.width(), max(1, bounds.width() - extra_width - 2 * margin)),
                    min(self.height(), max(1, bounds.height() - extra_height - 2 * margin)))
        frame = self.frameGeometry()
        x = max(bounds.left() + margin, min(frame.x(), bounds.right() - frame.width() + 1 - margin))
        y = max(bounds.top() + margin, min(frame.y(), bounds.bottom() - frame.height() + 1 - margin))
        self.move(x, y)

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
