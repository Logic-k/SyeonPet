"""One local focus/rest deadline, measured with an injectable monotonic clock."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QObject, QTimer, Signal


class FocusTimer(QObject):
    expired = Signal(str)
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("focusTimer")
        self.clock = time.monotonic
        self._deadline = None
        self.label = ""
        self.completed_label = ""
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.check)

    @property
    def remaining_seconds(self) -> int:
        if self._deadline is None:
            return 0
        return max(0, math.ceil(self._deadline - self.clock()))

    def start(self, minutes, label):
        seconds = float(minutes) * 60
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Timer duration must be finite and positive")
        self._deadline = self.clock() + seconds
        self.label = label
        self.completed_label = ""
        self._timer.start()
        self.changed.emit()

    def cancel(self):
        self._deadline = None
        self.label = ""
        self.completed_label = ""
        self._timer.stop()
        self.changed.emit()

    def check(self):
        if self._deadline is None:
            return
        if self.clock() >= self._deadline:
            label = self.label
            self._deadline = None
            self.label = ""
            self.completed_label = label
            self._timer.stop()
            self.expired.emit(label)
        self.changed.emit()
