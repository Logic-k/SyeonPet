"""션 데스크탑 펫 — entry point: app, tray menu, ambient chatter, autostart."""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from behavior import BehaviorController
from bubble import SpeechBubble
from chat import ChatPanel
from onboarding import OnboardingTour
from pet import ASSETS, PetWidget, SpriteBank

ROOT = Path(__file__).parent
# frozen exe: config.json next to SyeonPet.exe wins; bundled copy is the fallback
CONFIG_PATH = (Path(sys.executable).parent / "config.json"
               if getattr(sys, "frozen", False) else ROOT / "config.json")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "SyeonPet"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool):
    if sys.platform != "win32":
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        if enabled:
            exe = sys.executable if getattr(sys, "frozen", False) else str(
                Path(sys.executable).with_name("pythonw.exe"))
            cmd = f'"{exe}"'
            if not getattr(sys, "frozen", False):
                cmd += f' "{Path(__file__).resolve()}"'
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


def main() -> int:
    cfg = load_config()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("션 데스크탑 펫")

    sprites = SpriteBank(ASSETS)
    pet = PetWidget(sprites, display_size=int(cfg.get("display_size", 168)))
    pet.BLEND_MS = float(cfg.get("frame_blend_ms", 0))
    pet.crossfade = pet.BLEND_MS > 0
    pet.motion_amp = float(cfg.get("motion_amplitude", 0.8))
    bubble = SpeechBubble(pet)
    pet.bubble_widget = bubble
    behavior = BehaviorController(pet)
    behavior.base_walk_speed = float(cfg.get("walk_speed", 190))
    behavior.WALK_SPEED = behavior.base_walk_speed
    behavior.click_actions.update(cfg.get("click_actions", {}))
    tour = OnboardingTour(pet, behavior, bubble,
                          cfg.get("onboarding_steps", []))
    chat_panel = ChatPanel(pet, behavior, cfg)
    pet.show()

    # ---------- ambient chatter ----------

    ambient_lines = cfg.get("ambient_lines", [])
    ambient_state = {
        "on": bool(cfg.get("ambient_chatter", True)) and bool(ambient_lines)}
    ambient_until = 0.0

    def ambient_hide():
        bubble.hide()
        pet._relayout()

    def ambient_tick():
        nonlocal ambient_until
        if not ambient_state["on"] or tour.active or pet._dragging:
            return
        if time.monotonic() < ambient_until:
            return
        if behavior.mode == "idle" and random.random() < 0.35:
            bubble.show_step(random.choice(ambient_lines), 0, 1, False)
            pet._relayout()
            ambient_until = time.monotonic() + 5.0
            QTimer.singleShot(5000, ambient_hide)

    ambient_timer = QTimer()
    ambient_timer.setInterval(20000)
    ambient_timer.timeout.connect(ambient_tick)
    ambient_timer.start()

    # ---------- tray ----------

    tray = QSystemTrayIcon(QIcon(str(ASSETS / "base-source.png")))
    tray.setToolTip("션 데스크탑 펫")
    menu = QMenu()

    def toggle_tour():
        tour.finish() if tour.active else tour.start()

    act_tour = QAction("온보딩 시작 / 중지")
    act_tour.triggered.connect(toggle_tour)
    menu.addAction(act_tour)

    act_chat = QAction("션과 대화하기")
    act_chat.triggered.connect(chat_panel.show_near_pet)
    menu.addAction(act_chat)

    act_wander = QAction("돌아다니기")
    act_wander.triggered.connect(behavior.wander)
    menu.addAction(act_wander)

    act_calm = QAction("차분 모드 (천천히)")
    act_calm.setCheckable(True)
    act_calm.setChecked(bool(cfg.get("calm_mode", False)))
    behavior.set_slow(act_calm.isChecked())
    act_calm.triggered.connect(behavior.set_slow)
    menu.addAction(act_calm)

    act_sleep = QAction("재우기 / 깨우기")
    act_sleep.triggered.connect(
        lambda: behavior.idle(1.0) if behavior.mode in ("sleep", "deepsleep")
        else behavior.sleep())
    menu.addAction(act_sleep)

    act_liesleep = QAction("누워서 재우기")
    act_liesleep.triggered.connect(behavior.deep_sleep)
    menu.addAction(act_liesleep)

    act_still = QAction("완전히 가만히 재우기")
    act_still.triggered.connect(behavior.still_sleep)
    menu.addAction(act_still)

    act_celebrate = QAction("축하하기")
    act_celebrate.triggered.connect(
        lambda: behavior.play_one_shot("celebrate"))
    menu.addAction(act_celebrate)

    act_dance = QAction("춤추기")
    act_dance.triggered.connect(lambda: behavior.dance(4.8))
    menu.addAction(act_dance)

    act_jump = QAction("점프하기")
    act_jump.triggered.connect(
        lambda: behavior.play_one_shot("jump"))
    menu.addAction(act_jump)

    dbl_menu = menu.addMenu("더블클릭 동작")
    dbl_group = QActionGroup(dbl_menu)
    dbl_choices = [
        ("잠재우기", "sleep"), ("누워서 재우기", "liesleep"),
        ("축하하기", "celebrate"), ("춤추기", "dance"),
        ("점프하기", "jump"), ("인사하기", "wave"), ("없음", "none"),
    ]
    cur_dbl = behavior.click_actions.get("double_click", "sleep")
    for label, name in dbl_choices:
        a = QAction(label, dbl_menu, checkable=True)
        a.setChecked(name == cur_dbl)
        a.triggered.connect(
            lambda _c, n=name: behavior.click_actions.__setitem__(
                "double_click", n))
        dbl_group.addAction(a)
        dbl_menu.addAction(a)

    menu.addSeparator()

    def toggle_speech(checked):
        ambient_state["on"] = checked
        if not checked and not tour.active:
            bubble.hide()
            pet._relayout()

    act_speech = QAction("말풍선 표시")
    act_speech.setCheckable(True)
    act_speech.setChecked(ambient_state["on"])
    act_speech.triggered.connect(toggle_speech)
    menu.addAction(act_speech)

    act_through = QAction("클릭 통과 모드")
    act_through.setCheckable(True)
    act_through.triggered.connect(pet.set_click_through)
    menu.addAction(act_through)

    if sys.platform == "win32":
        act_auto = QAction("Windows 시작 시 자동 실행")
        act_auto.setCheckable(True)
        act_auto.setChecked(autostart_enabled())
        act_auto.triggered.connect(set_autostart)
        menu.addAction(act_auto)

    menu.addSeparator()
    act_quit = QAction("종료")
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_quit)

    tray.setContextMenu(menu)
    tray.show()

    if cfg.get("start_onboarding_on_launch", False):
        QTimer.singleShot(1200, tour.start)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
