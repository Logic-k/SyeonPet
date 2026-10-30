"""Windows Qt smoke test for first-use examples; uses synthetic data only.

No real user settings, library, clipboard, or AI/network services are touched.
The screenshots show Qt widgets and are not an OS mouse/real-provider test.
"""
from __future__ import annotations

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

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from behavior import BehaviorController
from chat import ChatPanel
from pet import PetWidget, SpriteBank


def contrast(a: QColor, b: QColor) -> float:
    def luminance(color):
        channels = (color.redF(), color.greenF(), color.blueF())
        values = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in channels]
        return .2126 * values[0] + .7152 * values[1] + .0722 * values[2]
    # Qt's placeholder palette may be translucent; compare the composited
    # displayed color against the base rather than the raw RGB components.
    alpha = a.alphaF()
    displayed = QColor.fromRgbF(*(alpha * x + (1 - alpha) * y for x, y in zip(
        (a.redF(), a.greenF(), a.blueF()), (b.redF(), b.greenF(), b.blueF()))))
    bright, dark = sorted((luminance(displayed), luminance(b)), reverse=True)
    return round((bright + .05) / (dark + .05), 2)


def run():
    if sys.platform != "win32":
        raise RuntimeError("A Windows Qt session is required")
    output = Path(tempfile.mkdtemp(prefix="syeon-first-example-"))
    os.environ["APPDATA"] = str(output / "appdata")
    os.environ["LOCALAPPDATA"] = str(output / "localappdata")
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    settings = QSettings(str(output / "conversation.ini"), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    wardrobe_settings = QSettings(str(output / "wardrobe.ini"), QSettings.Format.IniFormat)
    wardrobe_settings.setFallbacksEnabled(False)
    pet = PetWidget(SpriteBank(), wardrobe_settings=wardrobe_settings)
    pet.tick.stop()
    behavior = BehaviorController(pet)
    behavior.timer.stop()
    panel = ChatPanel(pet, behavior, {}, settings=settings)
    prompt = panel.prompt_conversation

    def blocked(*_args, **_kwargs):
        raise AssertionError("unexpected network, AI or clipboard use")

    def capture(name):
        app.processEvents()
        if not panel.grab().save(str(output / name)):
            raise RuntimeError("screenshot failed")

    try:
        with patch.object(socket, "getaddrinfo", side_effect=blocked), \
             patch.object(socket, "create_connection", side_effect=blocked), \
             patch.object(QApplication, "clipboard", side_effect=blocked), \
             patch("optimizer_chat.optimize_ai", side_effect=blocked):
            assert prompt.provider.currentData() == "vireonix"
            assert prompt.input.toPlainText() == ""
            first = prompt.input.placeholderText()
            assert "예" in first and len(first) > 10
            synthetic = first.removeprefix("예:").strip()
            assert synthetic
            for name, size in (("default.png", (460, 650)), ("small.png", (360, 450))):
                panel.resize(*size)
                panel.show()
                panel.raise_()
                panel.activateWindow()
                prompt.input.setFocus()
                capture(name)
                assert panel.width() == size[0] and panel.height() == size[1]
                assert prompt.input.isVisible() and prompt.input.toPlainText() == ""
                assert prompt.input.hasFocus(), "example must remain visible while the input is focused"

            prompt.send.click()
            app.processEvents()
            assert prompt.inline_notice.isVisible() and not prompt.is_busy
            assert prompt.input.toPlainText() == "" and not prompt.results
            assert getattr(prompt, "_confirmation", None) is None

            prompt.input.setPlainText(synthetic)
            app.processEvents()
            assert prompt.input.toPlainText() == synthetic
            assert not prompt.inline_notice.isVisible()
            capture("typed.png")
            prompt.send.click()
            app.processEvents()
            assert prompt._confirmation.isVisible()
            assert prompt.input.toPlainText() == synthetic
            prompt._confirmation.done(int(QMessageBox.StandardButton.No))
            app.processEvents()
            assert prompt.input.toPlainText() == synthetic and not prompt.is_busy
            prompt.provider.setCurrentIndex(prompt.provider.findData("offline"))
            prompt.send.click()
            deadline = time.monotonic() + 5
            while prompt.is_busy and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            app.processEvents()
            assert not prompt.is_busy and synthetic in prompt.result.toPlainText()
            assert prompt.original == synthetic and prompt.input.toPlainText() == ""
            followup = prompt.input.placeholderText()
            assert "예" in followup and followup != first
            capture("followup.png")
            palette = prompt.input.palette()
            ratio = contrast(palette.color(QPalette.ColorRole.PlaceholderText),
                             palette.color(QPalette.ColorRole.Base))
            assert ratio >= 4.5, f"example contrast is too low: {ratio}"
            print("PASS: examples shown for first request and follow-up with empty input")
            print("PASS: empty submit blocked; AI consent cancelled; offline result preserves the displayed example")
            print("FIRST:", repr(first))
            print("FOLLOWUP:", repr(followup))
            print("PLACEHOLDER_PALETTE_CONTRAST:", ratio)
            print("SCREENSHOTS:", output)
            print("LIMIT: Qt capture/synthetic events; no actual AI, clipboard, or OS mouse")
    finally:
        behavior.timer.stop()
        pet.tick.stop()
        panel.close()
        pet.close()
        app.quit()


if __name__ == "__main__":
    run()
