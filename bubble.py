"""Speech bubble — a child widget at the top of the pet window.

Not a top-level window: some WMs refuse to map a second frameless window, and
one window keeps z-order trivially correct. Clicks reach it because the pet's
setMask() includes its rect while visible.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class SpeechBubble(QWidget):
    next_clicked = Signal()
    prev_clicked = Signal()
    skip_clicked = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._generation = 0
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setStyleSheet(
            "QLabel { color: #2b2118; font-size: 13px; padding: 2px; }")

        self.btn_prev = QPushButton("◀")
        self.btn_next = QPushButton("다음 ▶")
        self.btn_skip = QPushButton("건너뛰기")
        for b in (self.btn_prev, self.btn_next, self.btn_skip):
            b.setStyleSheet(
                "QPushButton { color: #2b2118; background: #f2e9dd; border: 1px solid #c9b8a3;"
                " border-radius: 6px; padding: 3px 10px; font-size: 12px; }"
                "QPushButton:hover { background: #fff6ea; }")
        self.btn_prev.setFixedWidth(34)
        self.btn_prev.clicked.connect(self.prev_clicked)
        self.btn_next.clicked.connect(self.next_clicked)
        self.btn_skip.clicked.connect(self.skip_clicked)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.btn_prev)
        row.addWidget(self.btn_skip)
        row.addStretch(1)
        row.addWidget(self.btn_next)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(self.text)
        lay.addLayout(row)
        self.setFixedWidth(280)
        self.hide()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)
        p.fillPath(path, QColor(255, 252, 246, 245))
        p.setPen(QPen(QColor(90, 70, 55), 2))
        p.drawPath(path)
        p.end()

    def hide(self):
        self._generation += 1
        super().hide()
        parent = self.parentWidget()
        if getattr(parent, "bubble_widget", None) is self:
            parent._relayout()

    def show_step(self, text: str, index: int, total: int, interactive: bool):
        self._generation += 1
        self.text.setText(text)
        self.btn_prev.setVisible(interactive and index > 0)
        self.btn_skip.setVisible(interactive)
        self.btn_next.setText("완료" if index == total - 1 else "다음 ▶")
        self.btn_next.setVisible(interactive)
        self.adjustSize()
        self.show()

    def show_temporary(self, text: str, milliseconds: int = 5000):
        self.show_step(text, 0, 1, False)
        self.parentWidget()._relayout()
        self.parentWidget().raise_()
        generation = self._generation
        QTimer.singleShot(milliseconds, lambda: self._hide_temporary(generation))

    def _hide_temporary(self, generation):
        if generation == self._generation:
            self.hide()

    def anchor_to(self, pet_widget):
        """Re-parent is done at construction; just trigger window relayout."""
        pet_widget._relayout()
