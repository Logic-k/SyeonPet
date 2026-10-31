"""Opt-in Windows Qt layout check with synthetic content and isolated settings.

Produces screenshots in a temporary folder. No user library, settings, clipboard,
network, or pre-existing app process is read or modified. QT_SCALE_FACTOR can be
set before launch to exercise a Qt scale override; it is not a Windows text-scale
or high-contrast certification.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Lib" / "site-packages"))

from PySide6.QtCore import QEvent, QPoint, QRect, QSettings, Qt
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QScrollArea

from behavior import BehaviorController
from chat import ChatPanel
from pet import PetWidget, SpriteBank


def bounds_in_parent(widget, top):
    point = widget.mapTo(top, QPoint(0, 0))
    return [point.x(), point.y(), widget.width(), widget.height()]


def ratio(foreground, background):
    def luminance(color):
        channels = [color.redF(), color.greenF(), color.blueF()]
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
        return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]
    a, b = sorted((luminance(foreground), luminance(background)), reverse=True)
    return round((a + .05) / (b + .05), 2)


def inspect_widget(top, controls):
    report = {}
    for name, widget in controls.items():
        x, y, width, height = bounds_in_parent(widget, top)
        parent = widget.parentWidget()
        scroll_area = None
        while parent is not None and parent is not top:
            if isinstance(parent, QScrollArea):
                scroll_area = parent
                break
            parent = parent.parentWidget()
        report[name] = {"rect": [x, y, width, height], "visible": widget.isVisible(),
                        "within_window": x >= 0 and y >= 0 and x + width <= top.width()
                        and y + height <= top.height(),
                        "outer_scroll_max": (scroll_area.verticalScrollBar().maximum()
                                             if scroll_area else None)}
    return report


def overlaps(report):
    pairs = (("input", "send"), ("result", "status"), ("template", "library"),
             ("library", "reset"), ("result_choice", "copy"), ("conversation", "provider"))
    found = []
    for first, second in pairs:
        a, b = report[first], report[second]
        if a["visible"] and b["visible"] and QRect(*a["rect"]).intersects(QRect(*b["rect"])):
            found.append([first, second])
    return found


def capture(widget, path):
    QApplication.processEvents()
    if not widget.grab().save(str(path)):
        raise RuntimeError(f"failed to save {path}")


def run():
    if sys.platform != "win32":
        raise RuntimeError("A real Windows Qt session is required")
    output = Path(tempfile.mkdtemp(prefix="syeon-readability-"))
    os.environ["APPDATA"] = str(output / "appdata")
    os.environ["LOCALAPPDATA"] = str(output / "localappdata")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(output / "settings"))
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    if "--dark" in sys.argv:
        dark = QPalette()
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                     QPalette.ColorRole.Button, QPalette.ColorRole.AlternateBase):
            dark.setColor(role, QColor("#202020"))
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                     QPalette.ColorRole.ButtonText):
            dark.setColor(role, QColor("#ffffff"))
        app.setPalette(dark)
    settings = QSettings(str(output / "conversation.ini"), QSettings.Format.IniFormat)
    wardrobe_settings = QSettings(str(output / "wardrobe.ini"), QSettings.Format.IniFormat)
    wardrobe_settings.setFallbacksEnabled(False)
    pet = PetWidget(SpriteBank(), wardrobe_settings=wardrobe_settings)
    pet.tick.stop()
    behavior = BehaviorController(pet)
    behavior.timer.stop()
    chat = ChatPanel(pet, behavior, {}, settings=settings)
    prompt = chat.prompt_conversation
    controls = {name: getattr(prompt, name) for name in (
        "conversation", "provider", "provider_note", "task", "detail", "input",
        "template", "library", "reset", "send", "result_choice", "result", "copy", "status")}
    reports = {}

    def blocked(*_args, **_kwargs):
        raise AssertionError("network or clipboard must not be used in this test")

    try:
        with patch.object(socket, "create_connection", side_effect=blocked), \
             patch.object(socket, "getaddrinfo", side_effect=blocked), \
             patch.object(QApplication, "clipboard", side_effect=blocked), \
             patch("optimizer_chat.optimize_ai", side_effect=blocked):
            chat.show()
            prompt.input.setPlainText("회의 내용을 세 항목으로 정리해 주세요.")
            prompt.provider.setCurrentIndex(prompt.provider.findData("offline"))
            prompt.submit()
            deadline = time.monotonic() + 5
            while prompt.is_busy and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            app.processEvents()
            assert not prompt.is_busy and "회의 내용을" in prompt.result.toPlainText()
            prompt.input.setPlainText("결과에 담당자와 기한을 추가해 주세요.")
            for name, size in (("default", (460, 650)), ("small", (360, 450))):
                chat.resize(*size)
                app.processEvents()
                capture(chat, output / f"conversation-{name}.png")
                rects = inspect_widget(chat, controls)
                reports[name] = {"requested": size, "actual": [chat.width(), chat.height()],
                                 "controls": rects, "overlaps": overlaps(rects)}
                if name == "default":
                    prompt.history_toggle.click()
                    app.processEvents()
                    history_rects = inspect_widget(chat, controls)
                    reports["default_history"] = {
                        "actual": [chat.width(), chat.height()], "controls": history_rects,
                        "overlaps": overlaps(history_rects)}
                    capture(chat, output / "conversation-default-history.png")
                    prompt.history_toggle.click()
                    app.processEvents()
            prompt.history_toggle.click()
            chat.resize(360, 450)
            app.processEvents()
            capture(chat, output / "conversation-small-history.png")
            rects = inspect_widget(chat, controls)
            reports["small_history"] = {"requested": [360, 450],
                "actual": [chat.width(), chat.height()], "controls": rects, "overlaps": overlaps(rects)}
            chat.activateWindow()
            chat.raise_()
            app.processEvents()
            prompt.send.setFocus()
            app.processEvents()
            transitions = []
            for _ in range(4):
                focused = app.focusWidget()
                if focused is None:
                    break
                app.sendEvent(focused, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier))
                app.sendEvent(focused, QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier))
                app.processEvents()
                focused = app.focusWidget()
                if focused is not None:
                    transitions.append({"name": focused.accessibleName() or focused.objectName(),
                                        "rect": bounds_in_parent(focused, chat)})
            reports["small_history"]["tab_focus_sequence"] = transitions
            capture(chat, output / "conversation-small-history-focus.png")
            ancestor = prompt.result.parentWidget()
            while ancestor is not None and not isinstance(ancestor, QScrollArea):
                ancestor = ancestor.parentWidget()
            if ancestor is not None:
                ancestor.verticalScrollBar().setValue(ancestor.verticalScrollBar().maximum())
                app.processEvents()
                reports["small_history"]["after_scroll"] = inspect_widget(chat, {
                    "result": prompt.result, "status": prompt.status})
                capture(chat, output / "conversation-small-history-bottom.png")
                ancestor.verticalScrollBar().setValue(0)
                app.processEvents()
            prompt.history_toggle.click()
            app.processEvents()
            prompt.template.click()
            picker = prompt._template_dialog
            picker.office_button.click()
            picker.list.setCurrentRow(0)
            app.processEvents()
            capture(picker, output / "templates.png")
            reports["template"] = {"actual": [picker.width(), picker.height()],
                                   "controls": inspect_widget(picker, {
                                       name: getattr(picker, name) for name in (
                                           "office_button", "search", "category", "list", "preview", "select_button")})}
            ancestor = picker.preview.parentWidget()
            while ancestor is not None and not isinstance(ancestor, QScrollArea):
                ancestor = ancestor.parentWidget()
            if ancestor is not None:
                ancestor.ensureWidgetVisible(picker.preview, 0, 0)
                app.processEvents()
                reports["template"]["preview_after_scroll"] = inspect_widget(
                    picker, {"preview": picker.preview})["preview"]
                capture(picker, output / "templates-preview.png")
                picker.resize(360, 450)
                app.processEvents()
                # At the compact size the preview extends below the viewport.
                # Scroll explicitly to the end and verify that it becomes visible.
                ancestor.verticalScrollBar().setValue(ancestor.verticalScrollBar().maximum())
                app.processEvents()
                reports["template_small"] = {"requested": [360, 450],
                    "actual": [picker.width(), picker.height()],
                    "scroll_value": ancestor.verticalScrollBar().value(),
                    "controls": inspect_widget(picker, {"preview": picker.preview,
                                                         "select_button": picker.select_button})}
                capture(picker, output / "templates-small-preview.png")
            picker.reject()
            prompt.provider.setCurrentIndex(prompt.provider.findData("vireonix"))
            prompt.send.click()
            app.processEvents()
            confirmation = prompt._confirmation
            assert confirmation.isVisible(), "external AI consent dialog not shown"
            capture(confirmation, output / "confirmation.png")
            reports["confirmation"] = {"labels": []}
            for label in confirmation.findChildren(QLabel):
                if label.isVisible() and label.text():
                    fg = label.palette().color(QPalette.ColorRole.WindowText)
                    bg = confirmation.palette().color(QPalette.ColorRole.Window)
                    reports["confirmation"]["labels"].append({"text": label.text()[:80],
                        "foreground": fg.name(), "background": bg.name(), "ratio": ratio(fg, bg)})
            before_input = prompt.input.toPlainText()
            before_result = prompt.result.toPlainText()
            with patch("optimizer_chat.optimize_ai", side_effect=OSError("시험용 연결 실패")):
                confirmation.done(int(QMessageBox.StandardButton.Yes))
                deadline = time.monotonic() + 5
                while prompt.is_busy and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
                app.processEvents()
            assert not prompt.is_busy and "연결 실패 또는 정리 중 오류" in prompt.status.text()
            assert "시험용 연결 실패" not in prompt.status.text()
            assert prompt.input.toPlainText() == before_input
            assert prompt.result.toPlainText() == before_result
            capture(chat, output / "conversation-small-error.png")
            notice = prompt.inline_notice
            assert notice.isVisible() and "연결 실패 또는 정리 중 오류" in notice.text()
            error_report = inspect_widget(chat, {"notice": notice})["notice"]
            ancestor = notice.parentWidget()
            while ancestor is not None and not isinstance(ancestor, QScrollArea):
                ancestor = ancestor.parentWidget()
            if ancestor is not None:
                viewport = ancestor.viewport()
                viewport_rect = QRect(viewport.mapToGlobal(QPoint(0, 0)), viewport.size())
                notice_rect = QRect(notice.mapToGlobal(QPoint(0, 0)), notice.size())
                error_report["within_viewport"] = viewport_rect.contains(notice_rect)
                error_report["scroll_value"] = ancestor.verticalScrollBar().value()
            reports["small_error"] = error_report
            prompt.history_toggle.click()
            app.processEvents()
            prompt.send.click()
            assert prompt._confirmation.isVisible()
            with patch("optimizer_chat.optimize_ai", side_effect=OSError("시험용 재시도 실패")):
                prompt._confirmation.done(int(QMessageBox.StandardButton.Yes))
                deadline = time.monotonic() + 5
                while prompt.is_busy and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
                app.processEvents()
            assert not prompt.is_busy and "연결 실패 또는 정리 중 오류" in prompt.inline_notice.text()
            assert "시험용 재시도 실패" not in prompt.inline_notice.text()
            assert prompt.input.toPlainText() == before_input
            assert prompt.result.toPlainText() == before_result
            capture(chat, output / "conversation-small-history-error.png")
            notice_rect = QRect(prompt.inline_notice.mapToGlobal(QPoint(0, 0)), prompt.inline_notice.size())
            viewport_rect = QRect(ancestor.viewport().mapToGlobal(QPoint(0, 0)), ancestor.viewport().size())
            reports["small_history_error"] = {"within_viewport": viewport_rect.contains(notice_rect),
                "scroll_value": ancestor.verticalScrollBar().value()}
            prompt.history_toggle.click()
            app.processEvents()
            prompt.library.click()
            app.processEvents()
            library = prompt._library_dialog
            assert library.isVisible(), "library dialog not shown"
            capture(library, output / "library.png")
            reports["library"] = {"labels": []}
            for label in library.findChildren(QLabel):
                if label.isVisible() and label.text():
                    fg = label.palette().color(QPalette.ColorRole.WindowText)
                    bg = library.palette().color(QPalette.ColorRole.Window)
                    reports["library"]["labels"].append({"text": label.text()[:80],
                        "foreground": fg.name(), "background": bg.name(), "ratio": ratio(fg, bg)})
            library.reject()
            palette = prompt.input.palette()
            reports["input_contrast_from_palette"] = ratio(palette.color(QPalette.ColorRole.Text),
                                                           palette.color(QPalette.ColorRole.Base))
            screen = QGuiApplication.primaryScreen()
            reports["screen"] = {"device_pixel_ratio": screen.devicePixelRatio(),
                                 "logical_dpi": screen.logicalDotsPerInch(),
                                 "qt_scale_factor": os.environ.get("QT_SCALE_FACTOR")}
    finally:
        chat.close()
        pet.close()
        app.processEvents()
        settings.sync()
    print(json.dumps(reports, ensure_ascii=False))
    print("SCREENSHOTS:", output)
    print("LIMIT: Qt widget capture, synthetic data; no OS text scaling or real semi-transparent backdrop evaluation")
    assert reports["default"]["actual"] == [460, 650]
    assert reports["small"]["actual"] == [360, 450]
    assert reports["small_history"]["actual"] == [360, 450]
    for state in ("default", "default_history", "small", "small_history"):
        assert not reports[state]["overlaps"], f"controls overlap in {state}"
    for item in ("preview", "select_button"):
        assert reports["template"]["controls"][item]["within_window"]
        assert reports["template_small"]["controls"][item]["within_window"]
    assert reports["small_history"]["after_scroll"]["status"]["within_window"]
    assert reports["small_history"]["after_scroll"]["result"]["within_window"]
    assert reports["input_contrast_from_palette"] >= 4.5
    assert reports["small_error"].get("within_viewport", reports["small_error"]["within_window"]), \
        "inline error notice is outside the visible scroll viewport"
    assert reports["small_history_error"]["within_viewport"], \
        "inline error notice with expanded history is outside the visible scroll viewport"
    for dialog in ("confirmation", "library"):
        assert reports[dialog]["labels"] and all(label["ratio"] >= 4.5 for label in reports[dialog]["labels"])
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
