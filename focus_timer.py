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
        self._cycle = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.check)

    @property
    def remaining_seconds(self) -> int:
        if self._deadline is None:
            return 0
        return max(0, math.ceil(self._deadline - self.clock()))

    @property
    def cycle(self) -> dict | None:
        """Active pomodoro plan (focus/rest/long_rest/rounds/done) or None."""
        return self._cycle

    def _check_minutes(self, minutes) -> float:
        seconds = float(minutes) * 60
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Timer duration must be finite and positive")
        return seconds

    def _begin(self, minutes, label):
        self._deadline = self.clock() + self._check_minutes(minutes)
        self.label = label
        self.completed_label = ""
        self._timer.start()
        self.changed.emit()

    def start(self, minutes, label):
        self._cycle = None
        self._begin(minutes, label)

    def start_cycle(self, focus_minutes=25, rest_minutes=5, long_rest_minutes=15, rounds=4):
        """Pomodoro: focus → rest … repeating, a long rest every `rounds` focus blocks."""
        for m in (focus_minutes, rest_minutes, long_rest_minutes):
            self._check_minutes(m)
        self._cycle = {"focus": focus_minutes, "rest": rest_minutes,
                       "long_rest": long_rest_minutes, "rounds": max(1, int(rounds)), "done": 0}
        self._begin(focus_minutes, "집중")

    def _cycle_next(self, finished_label):
        c = self._cycle
        if finished_label == "집중":
            if c["done"] % c["rounds"] == 0:
                return c["long_rest"], "긴 휴식"
            return c["rest"], "휴식"
        return c["focus"], "집중"

    def cancel(self):
        self._cycle = None
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
            if self._cycle is not None and label == "집중":
                self._cycle["done"] += 1
            self._deadline = None
            self.label = ""
            self.completed_label = label
            self._timer.stop()
            self.expired.emit(label)
            if self._cycle is not None:
                self._begin(*self._cycle_next(label))
        self.changed.emit()
