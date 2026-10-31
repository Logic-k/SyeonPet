"""Read Windows motion preferences without changing system or saved app settings."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from typing import Callable, Final, SupportsInt

from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray, QCoreApplication, QObject, Signal

SPI_GETCLIENTAREAANIMATION: Final = 0x1042
WM_SETTINGCHANGE: Final = 0x001A


def read_windows_reduced_motion() -> bool | None:
    """Return reduction preference, or None when no setting could be read."""
    if sys.platform != "win32":
        return None
    enabled = wintypes.BOOL()
    query = ctypes.windll.user32.SystemParametersInfoW
    query.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]
    query.restype = wintypes.BOOL
    if not query(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0):
        return None
    return not bool(enabled.value)


class _MotionFilter(QAbstractNativeEventFilter):
    def __init__(self, owner: MotionPreference) -> None:
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type: QByteArray, message: SupportsInt) -> bool:
        if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            native = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if native.message == WM_SETTINGCHANGE:
                self.owner.refresh()
        return False


class MotionPreference(QObject):
    """Own the last known policy and its app-local notification subscription."""

    changed = Signal(bool)

    def __init__(self, app: QCoreApplication,
                 query: Callable[[], bool | None] | None = None) -> None:
        super().__init__(app)
        self.setObjectName("motionPreference")
        self.app = app
        self.query = query if query is not None else read_windows_reduced_motion
        self.reduced = False
        self.known = False
        self._filter = _MotionFilter(self)
        app.installNativeEventFilter(self._filter)
        app.aboutToQuit.connect(self.stop)
        self.refresh()

    def refresh(self) -> None:
        value = self.query()
        if value is None:
            return
        self.known = True
        if value != self.reduced:
            self.reduced = value
            self.changed.emit(value)

    def stop(self) -> None:
        self.app.removeNativeEventFilter(self._filter)
