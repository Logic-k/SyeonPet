"""션 데스크탑 펫 — entry point: app, tray menu, ambient chatter, autostart."""
from __future__ import annotations

import sys
# Never create unsigned cache files inside a verified update directory.
sys.dont_write_bytecode = True

import json
import os
import random
import time
from pathlib import Path
from version import APP_VERSION

# The original installation remains the launcher and keeps its config.json.
# Bootstrap before Qt creates any windows or event loop.
ROOT = Path(__file__).resolve().parent
if __name__ == "__main__":
    import updater_core
    import updater_switch
    from update_trust import PUBLIC_KEY_BYTES
    try:
        _exit_code = updater_switch.bootstrap(ROOT, PUBLIC_KEY_BYTES, updater_core.verify_staged,
                                              base_version=APP_VERSION)
        if _exit_code is not None:
            raise SystemExit(_exit_code)
    except Exception:
        if os.environ.get("SYEONPET_UPDATE_CHILD"):
            raise
        # A failed lock/state check cannot prove that a different version is
        # not running. Do not start an uncoordinated second copy.
        message = ("업데이트 상태를 확인하지 못했어요. 열려 있는 션의 내용을 저장하고 "
                   "모두 종료한 뒤 다시 실행해 주세요. 계속 문제가 생기면 기존 폴더를 "
                   "보존한 채 도움을 요청해 주세요.")
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "션 업데이트", 0x30)
        else:
            print(message, file=sys.stderr)
        raise SystemExit(1)

from PySide6.QtCore import QPoint, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from behavior import BehaviorController
from bubble import SpeechBubble
from chat import ChatPanel
from onboarding import OnboardingTour
from pet import ASSETS, PetWidget, SpriteBank
import updater_switch
from updater_ui import RestartDrafts, UpdatePreparer, prepare_quit
from wardrobe import WardrobeWindow

INSTALL_ROOT = updater_switch.installation_root(ROOT)
CONFIG_PATH = INSTALL_ROOT / "config.json"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "SyeonPet"


def popup_pet_menu(menu: QMenu, pet: PetWidget, position: QPoint) -> None:
    """Show the tray menu beside the pet, inside the current work area."""
    screen = (QGuiApplication.screenAt(position) or pet.screen()
              or QGuiApplication.primaryScreen())
    if screen is not None:
        area = screen.availableGeometry()
        size = menu.sizeHint()
        x = max(area.left(), min(position.x(), area.right() - size.width() + 1))
        y = max(area.top(), min(position.y(), area.bottom() - size.height() + 1))
        position = QPoint(x, y)
    # popup() avoids starting another event loop; the tray and pet share the
    # very same menu/actions so their checked states stay synchronized.
    menu.popup(position)


def bind_pet_menu(pet: PetWidget, menu: QMenu) -> None:
    pet.context_requested.connect(lambda position: popup_pet_menu(menu, pet, position))


def present_update_result(result: dict, tray: QSystemTrayIcon, dialogs: list) -> None:
    """Inform the user when a verified update is ready for the next start."""
    status = result.get("status")
    if not result.get("manual"):
        if status in ("ready", "available"):
            tray.showMessage("션 업데이트", result.get("message", "새 버전이 있습니다."))
        return

    if status == "ready":
        box = QMessageBox(QMessageBox.Icon.Information, "션 업데이트",
                          result.get("message", "업데이트를 준비했습니다."),
                          QMessageBox.StandardButton.Ok)
        dialogs.append(box)
        box.finished.connect(lambda _value: dialogs.remove(box))
        box.open()
        return

    release_url = QUrl(result.get("release_url", ""))
    official_release = (release_url.scheme() == "https"
                        and release_url.host().lower() == "github.com"
                        and not release_url.userInfo()
                        and release_url.port() in (-1, 443)
                        and release_url.path().startswith("/Logic-k/SyeonPet/releases/tag/")
                        and not release_url.query() and not release_url.fragment())
    if status == "available" and official_release:
        box = QMessageBox(QMessageBox.Icon.Information, "션 업데이트",
                          result.get("message", "새 버전이 있습니다.") +
                          "\n공식 GitHub 릴리스 페이지를 열까요?",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.finished.connect(lambda value: QDesktopServices.openUrl(release_url)
                             if value == QMessageBox.StandardButton.Yes else None)
    else:
        box = QMessageBox(QMessageBox.Icon.Information, "션 업데이트 확인",
                          result.get("message", "업데이트를 확인하지 못했습니다."),
                          QMessageBox.StandardButton.Ok)
    dialogs.append(box)
    box.finished.connect(lambda _value: dialogs.remove(box))
    box.open()


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
            exe = str(INSTALL_ROOT / "pythonw.exe")
            cmd = f'"{exe}"'
            cmd += f' "{INSTALL_ROOT / "main.py"}"'
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
    wardrobe_window = WardrobeWindow(pet)
    drafts = RestartDrafts(INSTALL_ROOT)
    try:
        restore_visible = drafts.restore(chat_panel)
    except Exception:
        # A candidate that cannot restore must fail before sending its ready
        # signal so the unchanged previous version can recover the snapshot.
        if os.environ.get("SYEONPET_UPDATE_CHILD"):
            return 1
        restore_visible = False
        _restore_error = True
    else:
        _restore_error = False
    if restore_visible:
        chat_panel.show_prompt_near_pet()
    chat_panel.prompt_conversation.busy_changed.connect(
        lambda busy: behavior.begin_thinking() if busy else behavior.end_thinking())
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
    update_checker = UpdatePreparer(INSTALL_ROOT, parent=app)
    update_dialogs: list = []
    update_checker.completed.connect(
        lambda result: present_update_result(result, tray, update_dialogs))

    def toggle_tour():
        if tour.active:
            tour.finish()
        else:
            behavior.wake()
            tour.start()

    act_tour = QAction("온보딩 시작 / 중지")
    act_tour.triggered.connect(toggle_tour)
    menu.addAction(act_tour)

    act_prompt = QAction("프롬프트 정리 열기")
    act_prompt.triggered.connect(chat_panel.show_prompt_near_pet)
    menu.addAction(act_prompt)

    def show_wardrobe():
        wardrobe_window.show()
        wardrobe_window.raise_()
        wardrobe_window.activateWindow()

    act_wardrobe = QAction("션이 옷장")
    act_wardrobe.triggered.connect(show_wardrobe)
    menu.addAction(act_wardrobe)

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
    act_sleep.triggered.connect(behavior.toggle_sleep)
    menu.addAction(act_sleep)

    act_liesleep = QAction("누워서 재우기")
    act_liesleep.triggered.connect(behavior.deep_sleep)
    menu.addAction(act_liesleep)

    act_still = QAction("완전히 가만히 재우기")
    def sleep_completely():
        if tour.active:
            tour.finish()
        behavior.still_sleep()
    act_still.triggered.connect(sleep_completely)
    menu.addAction(act_still)

    act_blend = QAction("프레임 부드럽게 전환")
    act_blend.setCheckable(True)
    act_blend.setChecked(pet.crossfade)
    def set_frame_blending(enabled):
        if enabled and pet.BLEND_MS <= 0:
            pet.BLEND_MS = 30.0
        pet.crossfade = enabled
        pet._update_mask()
        pet.update()
    act_blend.triggered.connect(set_frame_blending)
    menu.addAction(act_blend)

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
    act_update = QAction("업데이트 확인")
    act_update.triggered.connect(lambda: update_checker.check(manual=True))
    menu.addAction(act_update)

    act_quit = QAction("종료")
    def quit_app():
        try:
            pending = updater_switch.pending_version(INSTALL_ROOT)
        except Exception:
            box = QMessageBox(QMessageBox.Icon.Warning, "션 업데이트",
                              "업데이트 상태를 확인하지 못해 종료를 보류했어요. "
                              "작성 중인 내용을 보관하거나 복사한 뒤 다시 시도해 주세요.",
                              QMessageBox.StandardButton.Ok)
            update_dialogs.append(box)
            box.finished.connect(lambda _value: update_dialogs.remove(box))
            box.open()
            return

        def finish_quit():
            try:
                # A download can complete while the quit confirmation is open.
                prepare_quit(update_checker, drafts, chat_panel, INSTALL_ROOT)
            except Exception:
                box = QMessageBox(QMessageBox.Icon.Warning, "대화 보관 실패",
                                  "작성 중인 대화를 보관하지 못해 종료하지 않았어요. "
                                  "열린 확인창이나 보관함을 닫고 다시 시도해 주세요.",
                                  QMessageBox.StandardButton.Ok)
                update_dialogs.append(box)
                box.finished.connect(lambda _value: update_dialogs.remove(box))
                box.open()
                return
            app.quit()
        chat_panel.request_app_quit(finish_quit, preserve_for_update=bool(pending))

    act_quit.triggered.connect(quit_app)
    menu.addAction(act_quit)

    tray.setContextMenu(menu)
    bind_pet_menu(pet, menu)
    tray.show()
    # Downloads are verified and staged separately; application occurs on restart.
    QTimer.singleShot(2500, lambda: update_checker.check(manual=False))

    if _restore_error:
        QTimer.singleShot(1500, lambda: tray.showMessage(
            "션 업데이트", "업데이트 또는 대화를 확인하지 못했어요. 보관된 내용은 이 기기에 남아 있어요."))
    elif os.environ.get("SYEONPET_UPDATE_ROLLBACK") == "1":
        QTimer.singleShot(1500, lambda: tray.showMessage(
            "션 업데이트", "새 버전을 시작하지 못해 이전 버전으로 돌아왔어요. 보관한 대화는 이 기기에 남아 있어요."))
    if drafts.remaining_count():
        QTimer.singleShot(5000, lambda: tray.showMessage(
            "션 대화 복원", "다른 창의 대화도 보관되어 있어요. 션을 한 번 더 실행하면 복원할 수 있어요."))
    # Only mark a candidate healthy after all widgets and any draft are restored
    # and the normal event loop has started processing events.
    QTimer.singleShot(0, updater_switch.notify_ready)

    if cfg.get("start_onboarding_on_launch", False):
        # Do not start an automatic tour after the user explicitly put 션 to sleep.
        QTimer.singleShot(1200, lambda: None if behavior.fully_asleep else tour.start())

    exit_code = app.exec()
    if exit_code == 0:
        drafts.finish_cleanly()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
