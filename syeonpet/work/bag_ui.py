"""Syeon's Bag Workspace Widget.

Provides the bag task checklist, quick sticky memo, and integrated focus timer controls.
"""
from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from syeonpet.work.tasks_memo import BagTaskManager, TaskItem


class BagWorkspaceWidget(QWidget):
    """Integrated bag workspace with checklist, quick sticky note, and focus timer."""

    task_completed = Signal(str)  # Emits completed task title
    timer_started = Signal(int)   # Emits duration in seconds
    timer_tick = Signal(int)      # Emits remaining seconds
    timer_finished = Signal(str)  # Emits celebration message

    def __init__(
        self,
        storage_path: Path | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = BagTaskManager(storage_path)
        self.timer_seconds_left = 0
        self.active_timer_mode = ""

        # Internal timers
        self.focus_timer = QTimer(self)
        self.focus_timer.setInterval(1000)
        self.focus_timer.timeout.connect(self._on_timer_tick)

        self.memo_debounce_timer = QTimer(self)
        self.memo_debounce_timer.setSingleShot(True)
        self.memo_debounce_timer.setInterval(500)
        self.memo_debounce_timer.timeout.connect(self._save_memo_content)

        self._init_ui()
        self.refresh_tasks()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Header Title
        title_lbl = QLabel("🎒 션이 가방 속 할 일 & 빠른 메모장", self)
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title_lbl.setFont(title_font)
        layout.addWidget(title_lbl)

        # 1. Focus Timer Controls Bar
        timer_box = QFrame(self)
        timer_box.setFrameShape(QFrame.Shape.StyledPanel)
        t_layout = QHBoxLayout(timer_box)
        t_layout.setContentsMargins(8, 6, 8, 6)
        t_layout.setSpacing(6)

        t_title = QLabel("⏱️ 집중 타이머:", timer_box)
        t_title.setStyleSheet("font-weight: bold; color: #2C3E50;")
        t_layout.addWidget(t_title)

        self.lbl_countdown = QLabel("25:00", timer_box)
        self.lbl_countdown.setStyleSheet("font-size: 14px; font-weight: bold; color: #E67E22;")
        t_layout.addWidget(self.lbl_countdown)

        btn_25 = QPushButton("🍅 25분 집중", timer_box)
        btn_25.clicked.connect(lambda: self.start_focus_timer(25, "집중"))
        t_layout.addWidget(btn_25)

        btn_50 = QPushButton("⏱️ 50분", timer_box)
        btn_50.clicked.connect(lambda: self.start_focus_timer(50, "집중"))
        t_layout.addWidget(btn_50)

        btn_10 = QPushButton("⏱️ 10분", timer_box)
        btn_10.clicked.connect(lambda: self.start_focus_timer(10, "집중"))
        t_layout.addWidget(btn_10)

        btn_5 = QPushButton("☕ 5분 휴식", timer_box)
        btn_5.clicked.connect(lambda: self.start_focus_timer(5, "휴식"))
        t_layout.addWidget(btn_5)

        btn_stop = QPushButton("⏹️ 중지", timer_box)
        btn_stop.clicked.connect(self.stop_focus_timer)
        t_layout.addWidget(btn_stop)

        layout.addWidget(timer_box)

        # 2. Quick Task Input Bar
        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self.txt_task = QLineEdit(self)
        self.txt_task.setPlaceholderText("할 일 내용을 적어주세요 (예: 금요일 오전까지 연수 안내장 발송)")
        self.txt_task.returnPressed.connect(self._on_add_task_clicked)
        input_row.addWidget(self.txt_task, stretch=4)

        self.cmb_due = QComboBox(self)
        today = datetime.date.today()
        tomorrow = today + datetime.timedelta(days=1)
        # Calculate upcoming Friday
        days_ahead = 4 - today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        this_friday = today + datetime.timedelta(days=days_ahead)

        self.cmb_due.addItem("기한 없음", "")
        self.cmb_due.addItem(f"오늘 마감 ({today.isoformat()})", today.isoformat())
        self.cmb_due.addItem(f"내일 마감 ({tomorrow.isoformat()})", tomorrow.isoformat())
        self.cmb_due.addItem(f"이번 주 금요일 ({this_friday.isoformat()})", this_friday.isoformat())
        input_row.addWidget(self.cmb_due, stretch=2)

        btn_add = QPushButton("➕ 추가", self)
        btn_add.setStyleSheet("font-weight: bold; padding: 4px 12px; background-color: #3498DB; color: white;")
        btn_add.clicked.connect(self._on_add_task_clicked)
        input_row.addWidget(btn_add, stretch=1)

        layout.addLayout(input_row)

        # 3. Tasks Checklist Scroll Area
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet("background-color: transparent;")

        self.task_list_container = QWidget()
        self.task_list_layout = QVBoxLayout(self.task_list_container)
        self.task_list_layout.setContentsMargins(0, 0, 0, 0)
        self.task_list_layout.setSpacing(6)
        self.task_list_layout.addStretch(1)

        self.scroll_area.setWidget(self.task_list_container)
        layout.addWidget(self.scroll_area, stretch=3)

        # 4. Quick Sticky Memo Area
        memo_header = QLabel("📝 가방 속 빠른 메모장 (내선번호, 공문번호, 임시 메모 등)", self)
        memo_header.setStyleSheet("font-weight: bold; color: #34495E;")
        layout.addWidget(memo_header)

        self.memo_edit = QPlainTextEdit(self)
        self.memo_edit.setPlaceholderText("자주 찾는 전화번호나 공문 번호, 중요한 메모를 자유롭게 적어두세요 (자동 저장됩니다)")
        self.memo_edit.setPlainText(self.manager.get_memo())
        self.memo_edit.textChanged.connect(self._on_memo_changed)
        layout.addWidget(self.memo_edit, stretch=2)

    def _on_add_task_clicked(self) -> None:
        title = self.txt_task.text().strip()
        if not title:
            return
        due_date = self.cmb_due.currentData() or ""
        self.manager.add_task(title, due_date)
        self.txt_task.clear()
        self.refresh_tasks()

    def refresh_tasks(self) -> None:
        """Clear and rebuild task items widgets."""
        # Clear existing items except stretch
        while self.task_list_layout.count() > 1:
            child = self.task_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        tasks = self.manager.get_tasks()
        if not tasks:
            empty_lbl = QLabel("✨ 가방에 등록된 할 일이 없어요. 새로운 할 일을 추가해 보세요!", self)
            empty_lbl.setStyleSheet("color: #7F8C8D; padding: 12px;")
            self.task_list_layout.insertWidget(0, empty_lbl)
            return

        for idx, task in enumerate(tasks):
            item_widget = self._create_task_row(task)
            self.task_list_layout.insertWidget(idx, item_widget)

    def _create_task_row(self, task: TaskItem) -> QWidget:
        row = QFrame(self.task_list_container)
        row.setFrameShape(QFrame.Shape.StyledPanel)
        row.setStyleSheet(
            "QFrame { background-color: #FAFAFA; border: 1px solid #E0E0E0; border-radius: 6px; padding: 4px; }"
            if not task.done else
            "QFrame { background-color: #F0F3F4; border: 1px dashed #BDC3C7; border-radius: 6px; padding: 4px; }"
        )
        r_layout = QHBoxLayout(row)
        r_layout.setContentsMargins(6, 4, 6, 4)
        r_layout.setSpacing(8)

        # Checkbox
        chk = QCheckBox(row)
        chk.setChecked(task.done)
        chk.toggled.connect(lambda checked, tid=task.id, ttitle=task.title: self._on_task_toggled(tid, ttitle, checked))
        r_layout.addWidget(chk)

        # Badge
        badge_lbl = QLabel(task.status_badge, row)
        badge_lbl.setStyleSheet("font-weight: bold; font-size: 11px;")
        r_layout.addWidget(badge_lbl)

        # Title
        title_lbl = QLabel(task.title, row)
        title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        if task.done:
            title_lbl.setStyleSheet("color: #7F8C8D; text-decoration: line-through;")
        else:
            title_lbl.setStyleSheet("color: #2C3E50; font-weight: 500;")
        r_layout.addWidget(title_lbl, stretch=1)

        # Due Date info
        if task.due_date:
            due_lbl = QLabel(task.due_date, row)
            due_lbl.setStyleSheet("color: #7F8C8D; font-size: 11px;")
            r_layout.addWidget(due_lbl)

        # Delete button
        btn_del = QPushButton("🗑️", row)
        btn_del.setToolTip("할 일 삭제")
        btn_del.setFixedWidth(28)
        btn_del.setStyleSheet("border: none; background: transparent;")
        btn_del.clicked.connect(lambda _, tid=task.id: self._on_delete_task(tid))
        r_layout.addWidget(btn_del)

        return row

    def _on_task_toggled(self, task_id: str, title: str, checked: bool) -> None:
        self.manager.update_task(task_id, done=checked)
        if checked:
            self.task_completed.emit(title)
        self.refresh_tasks()

    def _on_delete_task(self, task_id: str) -> None:
        self.manager.delete_task(task_id)
        self.refresh_tasks()

    def _on_memo_changed(self) -> None:
        self.memo_debounce_timer.start()

    def _save_memo_content(self) -> None:
        self.manager.set_memo(self.memo_edit.toPlainText())

    # --- Timer Control Methods ---
    def start_focus_timer(self, minutes: int, mode_name: str = "집중") -> None:
        """Start countdown timer for specified minutes."""
        self.active_timer_mode = mode_name
        self.timer_seconds_left = minutes * 60
        self._update_timer_display()
        self.focus_timer.start()
        self.timer_started.emit(self.timer_seconds_left)

    def stop_focus_timer(self) -> None:
        """Stop running countdown timer."""
        self.focus_timer.stop()
        self.timer_seconds_left = 0
        self.lbl_countdown.setText("--:--")

    def _on_timer_tick(self) -> None:
        if self.timer_seconds_left > 0:
            self.timer_seconds_left -= 1
            self._update_timer_display()
            self.timer_tick.emit(self.timer_seconds_left)
            if self.timer_seconds_left == 0:
                self.focus_timer.stop()
                msg = f"🎉 {self.active_timer_mode} 시간이 끝났어요! 수고하셨습니다 ✨"
                self.timer_finished.emit(msg)
        else:
            self.focus_timer.stop()

    def _update_timer_display(self) -> None:
        mins = self.timer_seconds_left // 60
        secs = self.timer_seconds_left % 60
        self.lbl_countdown.setText(f"{mins:02d}:{secs:02d}")
