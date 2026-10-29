"""Chat panel — a small always-on-top window that docks beside the pet.

Sends through the brain from providers.make_brain(): a real LLM when an API
key is configured, otherwise the offline canned responder. Replies land in
the panel and the pet celebrates briefly.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QHBoxLayout, QLineEdit, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget)

from providers import DEFAULT_PERSONA, make_brain


class _Worker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, brain, history, text, persona, parent=None):
        super().__init__(parent)
        self._args = (brain, history, text, persona)

    def run(self):
        brain, history, text, persona = self._args
        try:
            self.done.emit(brain.reply(history, text, persona))
        except Exception as e:  # network/auth/quota — show, don't crash
            self.failed.emit(str(e))


class ChatPanel(QWidget):
    def __init__(self, pet, behavior, cfg: dict):
        super().__init__()
        self.pet = pet
        self.behavior = behavior
        self.brain = make_brain(cfg)
        c = cfg.get("chat", {}) or {}
        self.persona = (c.get("persona") or DEFAULT_PERSONA)
        self.history: list[dict] = []
        self._worker: _Worker | None = None

        self.setWindowTitle("션과 대화")
        self.setWindowFlags(Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedSize(320, 400)

        self.view = QTextBrowser()
        self.view.setStyleSheet(
            "QTextBrowser { background: #fffdf8; border: none; font-size: 13px; }")
        self.input = QLineEdit()
        self.input.setPlaceholderText("션에게 말 걸기…")
        self.input.returnPressed.connect(self._send)
        self.btn = QPushButton("전송")
        self.btn.clicked.connect(self._send)

        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.view, 1)
        lay.addLayout(row)

        self._append("션", self._greeting())

    def _greeting(self) -> str:
        if self.brain.name == "canned":
            return ("안녕하세요! 션이에요 🌱 지금은 간단한 대답만 할 수 있어요. "
                    "config.json에 Gemini/Groq API 키를 넣으면 진짜 AI로 "
                    "대화할 수 있어요!")
        return "안녕하세요! 션이에요 🌱 무엇이든 물어보세요!"

    def _append(self, who: str, text: str):
        me = who == "나"
        color = "#5a4637" if me else "#2b6cb0"
        bg = "#f2e9dd" if me else "#e8f1fb"
        align = "right" if me else "left"
        self.view.append(
            f"<div style='margin:4px 0; text-align:{align}'>"
            f"<span style='font-size:10px; color:{color}'>{who}</span><br>"
            f"<span style='background:{bg}; border-radius:8px; "
            f"padding:5px 9px; display:inline-block; max-width:230px; "
            f"text-align:left'>{text}</span></div>")
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _send(self):
        text = self.input.text().strip()
        if not text or (self._worker and self._worker.isRunning()):
            return
        self.input.clear()
        self._append("나", text)
        self._append("션", "<i>생각 중…</i>")
        self._worker = _Worker(self.brain, list(self.history), text,
                               self.persona, self)
        self._worker.done.connect(lambda r: self._finish(text, r))
        self._worker.failed.connect(self._fail)
        self._worker.start()

    def _pop_thinking(self):
        html = self.view.toHtml()
        i = html.rfind("생각 중…")
        if i != -1:
            html = html[:html.rfind("<div", 0, i)] + "</body></html>"
            self.view.setHtml(html)

    def _finish(self, user_text: str, reply: str):
        self._pop_thinking()
        self._append("션", reply)
        self.history.append({"role": "user", "text": user_text})
        self.history.append({"role": "assistant", "text": reply})
        self.history = self.history[-20:]
        if self.behavior.mode in ("idle", "sleep"):
            self.behavior.play_one_shot("wave")

    def _fail(self, err: str):
        self._pop_thinking()
        self._append("션", f"미안해요, 연결이 안 됐어요… ({err[:120]})")

    def show_near_pet(self):
        geo = self.pet.sprite_rect_global()
        screen = QGuiApplication.screenAt(geo.center()) \
            or QGuiApplication.primaryScreen()
        b = screen.availableGeometry()
        x = geo.right() + 8
        if x + self.width() > b.right():
            x = geo.left() - 8 - self.width()
        x = max(b.left() + 4, min(x, b.right() - self.width() - 4))
        y = max(b.top() + 4, min(geo.top() - 20, b.bottom() - self.height() - 4))
        self.move(x, y)
        self.show()
        self.raise_()
        self.input.setFocus()
