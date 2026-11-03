"""SyeonPet Web AI Bridge.

Enables one-click copy and browser navigation to popular web AI services
(ChatGPT, Wrtn, Gemini, Claude) with friendly novice guidance.
"""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

WEB_AI_TARGETS: list[tuple[str, str, str]] = [
    ("chatgpt", "ChatGPT 열기", "https://chatgpt.com/"),
    ("wrtn", "뤼튼(Wrtn · 한국어 무료) 열기", "https://wrtn.ai/"),
    ("gemini", "Google 제미나이 열기", "https://gemini.google.com/"),
    ("claude", "Claude 열기", "https://claude.ai/"),
]

GUIDE_MESSAGE = "클립보드에 복사했어요! 열린 인터넷 창 입력칸을 누르고 Ctrl+V (붙여넣기)만 누르시면 돼요."


def get_web_ai_targets() -> list[tuple[str, str, str]]:
    """Return list of supported Korean web AI targets."""
    return list(WEB_AI_TARGETS)


def get_target_url(target_id: str) -> str:
    """Return URL string for the specified target id, defaulting to ChatGPT."""
    for tid, _, url in WEB_AI_TARGETS:
        if tid == target_id:
            return url
    return "https://chatgpt.com/"


def copy_and_open_web_ai(
    text: str,
    target_id: str = "chatgpt",
    open_browser: bool = True,
) -> str:
    """Copy content to system clipboard and launch the chosen Web AI service.

    Returns a friendly step-by-step guidance message for novice users.
    """
    app = QApplication.instance()
    if app:
        clipboard = app.clipboard()
        if clipboard:
            clipboard.setText(text)

    url_str = get_target_url(target_id)
    if open_browser:
        QDesktopServices.openUrl(QUrl(url_str))

    return GUIDE_MESSAGE
