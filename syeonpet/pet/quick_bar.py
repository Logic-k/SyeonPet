"""Floating Quick Action Pill Bar and Pet Timer Badge.

Solves the primary novice discovery challenge by presenting accessible quick actions
directly above Syeon upon a single click, and renders a live Pomodoro badge.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget


class QuickActionBar(QWidget):
    """Frameless, translucent pill toolbar floating near Syeon on single-click."""

    open_work = Signal()
    open_finder = Signal()
    open_bag = Signal()
    toggle_timer = Signal()
    open_wardrobe = Signal()

    AUTO_HIDE_MS: int = 6000

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._timer_active = False

        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.setInterval(self.AUTO_HIDE_MS)
        self._auto_hide_timer.timeout.connect(self.hide)

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet("""
            QWidget#BarContainer {
                background-color: rgba(255, 253, 249, 0.96);
                border: 1px solid #d7e3ee;
                border-radius: 18px;
            }
            QPushButton {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 13px;
                padding: 6px 11px;
                font-family: '맑은 고딕', 'Malgun Gothic', sans-serif;
                font-size: 10pt;
                font-weight: bold;
                color: #2b3948;
            }
            QPushButton:hover {
                background-color: #eef5fb;
                border-color: #c9dbed;
            }
            QPushButton:pressed {
                background-color: #ddeaf5;
            }
            QPushButton#TimerBtnActive {
                background-color: #fff1ed;
                border-color: #ffc4b3;
                color: #d94824;
            }
            QPushButton#TimerBtnActive:hover {
                background-color: #ffe5de;
            }
        """)

        container = QWidget(self)
        container.setObjectName("BarContainer")

        layout = QHBoxLayout(container)
        layout.setContentsMargins(7, 4, 7, 4)
        layout.setSpacing(4)

        self.btn_work = QPushButton("✨ 도움받기", container)
        self.btn_work.setToolTip("업무 도움받기 창 열기")
        self.btn_work.clicked.connect(self._on_work_clicked)
        layout.addWidget(self.btn_work)

        self.btn_finder = QPushButton("🔍 파일찾기", container)
        self.btn_finder.setToolTip("초고속 파일 찾기 열기")
        self.btn_finder.clicked.connect(self._on_finder_clicked)
        layout.addWidget(self.btn_finder)

        self.btn_bag = QPushButton("🎒 할일·메모", container)
        self.btn_bag.setToolTip("션 가방(할 일·메모) 열기")
        self.btn_bag.clicked.connect(self._on_bag_clicked)
        layout.addWidget(self.btn_bag)

        self.btn_timer = QPushButton("⏱️ 25분 집중", container)
        self.btn_timer.setToolTip("25분 집중 타이머 시작 / 중지")
        self.btn_timer.clicked.connect(self._on_timer_clicked)
        layout.addWidget(self.btn_timer)

        self.btn_wardrobe = QPushButton("👗 옷장", container)
        self.btn_wardrobe.setToolTip("옷장 열기")
        self.btn_wardrobe.clicked.connect(self._on_wardrobe_clicked)
        layout.addWidget(self.btn_wardrobe)

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(container)

        self.adjustSize()

    def set_timer_active(self, is_active: bool) -> None:
        """Update timer pill button label and styling based on active state."""
        self._timer_active = bool(is_active)
        if self._timer_active:
            self.btn_timer.setText("⏱️ 타이머 중지")
            self.btn_timer.setObjectName("TimerBtnActive")
        else:
            self.btn_timer.setText("⏱️ 25분 집중")
            self.btn_timer.setObjectName("")
        self.btn_timer.style().unpolish(self.btn_timer)
        self.btn_timer.style().polish(self.btn_timer)
        self.adjustSize()

    def fit_near_pet(self, pet_geometry: QRect, screen_available_geometry: QRect) -> None:
        """Position toolbar near pet while keeping it fully on screen."""
        self.adjustSize()
        bar_w = self.width()
        bar_h = self.height()

        # Center horizontally over pet
        target_x = pet_geometry.center().x() - (bar_w // 2)

        # Prefer placing above pet; flip below if clipping top edge
        target_y = pet_geometry.top() - bar_h - 8
        if target_y < screen_available_geometry.top():
            target_y = pet_geometry.bottom() + 8

        # Clamp within screen bounds
        min_x = screen_available_geometry.left() + 4
        max_x = max(min_x, screen_available_geometry.right() - bar_w - 4)
        clamped_x = max(min_x, min(target_x, max_x))

        min_y = screen_available_geometry.top() + 4
        max_y = max(min_y, screen_available_geometry.bottom() - bar_h - 4)
        clamped_y = max(min_y, min(target_y, max_y))

        self.setGeometry(clamped_x, clamped_y, bar_w, bar_h)

    def show_near_pet(self, pet_geometry: QRect, screen_available_geometry: QRect) -> None:
        """Reposition, reveal the bar, and arm the auto-hide timer."""
        self.fit_near_pet(pet_geometry, screen_available_geometry)
        self.show()
        self._auto_hide_timer.start()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._auto_hide_timer.stop()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._auto_hide_timer.start()

    def _on_work_clicked(self) -> None:
        self.hide()
        self.open_work.emit()

    def _on_finder_clicked(self) -> None:
        self.hide()
        self.open_finder.emit()

    def _on_bag_clicked(self) -> None:
        self.hide()
        self.open_bag.emit()

    def _on_timer_clicked(self) -> None:
        self._auto_hide_timer.start()
        self.toggle_timer.emit()

    def _on_wardrobe_clicked(self) -> None:
        self.hide()
        self.open_wardrobe.emit()


def format_badge_text(label: str, remaining_seconds: int) -> str:
    """Format timer label and remaining seconds into a compact pill string."""
    mins = max(0, remaining_seconds) // 60
    secs = max(0, remaining_seconds) % 60
    lbl = (label or "").strip().lower()
    if lbl in ("집중", "focus"):
        return f"🍅 {mins:02d}:{secs:02d}"
    if lbl in ("휴식", "rest"):
        return f"☕ {mins:02d}:{secs:02d}"
    return f"⏱️ {mins:02d}:{secs:02d}"


def paint_timer_badge(painter: QPainter, rect: QRect, text: str) -> None:
    """Render a high-contrast pill badge inside rect."""
    if not text:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Translucent rounded pill background
    bg_color = QColor(24, 30, 38, 215)
    border_color = QColor(255, 255, 255, 80)
    painter.setPen(QPen(border_color, 1.2))
    painter.setBrush(QBrush(bg_color))
    radius = rect.height() / 2.0
    painter.drawRoundedRect(rect, radius, radius)

    # Centered white bold text
    font = QFont("맑은 고딕", 9, QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    painter.restore()


class TimerBadge:
    """Namespace / helper for timer badge formatting and drawing."""

    format = staticmethod(format_badge_text)
    paint = staticmethod(paint_timer_badge)
