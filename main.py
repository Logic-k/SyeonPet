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

ROOT = Path(__file__).resolve().parent
if __name__ == "__main__":
    import updater_core
    import updater_switch
    from update_trust import PUBLIC_KEY_BYTES
    try:
        updater_switch.wait_for_restart(ROOT)
        _exit_code = updater_switch.bootstrap(ROOT, PUBLIC_KEY_BYTES, updater_core.verify_staged,
                                              base_version=APP_VERSION)
        if _exit_code is not None:
            raise SystemExit(_exit_code)
    except Exception:
        if os.environ.get("SYEONPET_UPDATE_CHILD"):
            raise
        message = ("업데이트 상태를 확인하지 못했어요. 열려 있는 션의 내용을 저장하고 "
                   "모두 종료한 뒤 다시 실행해 주세요. 계속 문제가 생기면 기존 폴더를 "
                   "보존한 채 도움을 요청해 주세요.")
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "션 업데이트", 0x30)
        else:
            print(message, file=sys.stderr)
        raise SystemExit(1)

from PySide6.QtCore import QObject, QPoint, QSettings, QTimer, QUrl, SIGNAL
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QMessageBox, QSystemTrayIcon, QWidgetAction

from behavior import BehaviorController
from bubble import SpeechBubble
from chat import ChatPanel
from focus_timer import FocusTimer
from motion_policy import MotionPreference, effective_reduced_motion
from onboarding import OnboardingTour
from pet import ASSETS, PetWidget, SpriteBank
from syeonpet.pet.companion_care import OfficeCareScheduler
from syeonpet.pet.quick_bar import QuickActionBar, format_badge_text
from syeonpet.ui.pet_menu import (
    add_focus_timer_menu,
    build_motion_menu,
    build_settings_menu,
    build_timer_menu,
)
from update_checker import UpdateChecker
import updater_switch
from updater_ui import RestartDrafts, UpdatePreparer, prepare_quit
from wardrobe import WardrobeWindow

INSTALL_ROOT = updater_switch.installation_root(ROOT)
CONFIG_PATH = INSTALL_ROOT / "config.json"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "SyeonPet"
DOUBLE_CLICK_CHOICES = (
    ("비서 열기", "open_hub"),
    ("잠재우기", "sleep"), ("누워서 재우기", "liesleep"),
    ("축하하기", "celebrate"), ("춤추기", "dance"),
    ("점프하기", "jump"), ("인사하기", "wave"),
    ("기지개", "stretch"), ("꾸벅 인사", "bow"), ("없음", "none"),
)


def load_pet_preferences(settings, cfg):
    """Existing controls use their own store; config.json stays untouched."""
    def boolean(key, default):
        value = settings.value(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        return default

    allowed = {name for _label, name in DOUBLE_CLICK_CHOICES}
    raw_cfg_double = cfg.get("click_actions", {}).get("double_click", "open_hub")
    default_double = raw_cfg_double if raw_cfg_double in allowed else "sleep"
    double = settings.value("double_click", default_double)
    if not isinstance(double, str) or double not in allowed:
        double = default_double
    return {
        "calm": boolean("calm", bool(cfg.get("calm_mode", False))),
        "speech": boolean("speech", bool(cfg.get("ambient_chatter", True))),
        "blend": boolean("blend", float(cfg.get("frame_blend_ms", 0)) > 0),
        "double_click": double,
        "reduced_motion": boolean("reduced_motion", False),
        "follow_windows_motion": boolean("follow_windows_motion", True),
    }


def show_companion_message(behavior, bubble, speech_enabled, text):
    """A bubble only: wake ordinary sleep for important alerts, never disturb full sleep."""
    if (not speech_enabled or behavior.fully_asleep or behavior.onboarding_active
            or behavior.pet._dragging or behavior._fell):
        return False
    if behavior.sleeping:
        behavior.wake()
    elif behavior.pet.state == "nuzzle":
        behavior.idle(reset_count=False)  # a petting nuzzle never holds back an alert
    elif behavior.mode not in ("idle", "walk", "thinking"):
        return False
    saved = behavior._thinking_restore
    if saved is not None and saved["mode"] not in ("idle", "walk"):
        return False
    bubble.show_temporary(text)
    return True


def task_feedback(behavior, bubble, speech_enabled, outcome):
    lines = {
        "success": "정리가 끝났어요. 함께 확인해요!",
        "failure": "이번에는 마치지 못했어요. 잠깐 쉬어도 좋아요.",
        "copied": "복사했어요. 필요한 곳에 붙여 넣어요!",
    }
    if not speech_enabled or behavior.sleeping:
        return False
    text = lines.get(outcome)
    return show_companion_message(behavior, bubble, speech_enabled, text) if text else False


def popup_pet_menu(menu: QMenu, pet: PetWidget, position: QPoint) -> None:
    """Show the tray menu beside the pet, inside the current work area."""
    screen = QGuiApplication.screenAt(position) or pet.screen() or QGuiApplication.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        size = menu.sizeHint()
        x = max(area.left(), min(position.x(), area.right() - size.width() + 1))
        y = max(area.top(), min(position.y(), area.bottom() - size.height() + 1))
        position = QPoint(x, y)
    menu.popup(position)


def bind_pet_menu(pet: PetWidget, menu: QMenu) -> None:
    pet.context_requested.connect(lambda position: popup_pet_menu(menu, pet, position))


def present_update_result(result: dict, tray: QSystemTrayIcon, dialogs: list) -> None:
    status = result.get("status")
    if not result.get("manual"):
        if status in ("ready", "available"):
            tray.showMessage("션 업데이트", result.get("message", "새 버전이 있습니다."))
        return
    if status == "ready":
        box = QMessageBox(QMessageBox.Icon.Information, "션 업데이트",
                          result.get("message", "업데이트를 준비했습니다."), QMessageBox.StandardButton.Ok)
        dialogs.append(box)
        box.finished.connect(lambda _value: dialogs.remove(box))
        box.open()
        return
    release_url = QUrl(result.get("release_url", ""))
    official = (release_url.scheme() == "https" and release_url.host().lower() == "github.com"
                and not release_url.userInfo() and release_url.port() in (-1, 443)
                and release_url.path().startswith("/Logic-k/SyeonPet/releases/tag/")
                and not release_url.query() and not release_url.fragment())
    if status == "available" and official:
        box = QMessageBox(QMessageBox.Icon.Information, "션 업데이트",
                          result.get("message", "새 버전이 있습니다.") + "\n공식 GitHub 릴리스 페이지를 열까요?",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.finished.connect(lambda val: QDesktopServices.openUrl(release_url) if val == QMessageBox.StandardButton.Yes else None)
    else:
        box = QMessageBox(QMessageBox.Icon.Information, "션 업데이트 확인",
                          result.get("message", "업데이트를 확인하지 못했습니다."), QMessageBox.StandardButton.Ok)
    dialogs.append(box)
    box.finished.connect(lambda _val: dialogs.remove(box))
    box.open()


RESTART_COUNTDOWN_SECONDS = 15


def offer_update_restart(version: str, restart, dialogs: list,
                         seconds: int = RESTART_COUNTDOWN_SECONDS) -> QMessageBox | None:
    """Count down to an automatic restart; 나중에 keeps the next-launch behavior."""
    if any(getattr(box, "_restart_offer", False) for box in dialogs):
        return None
    box = QMessageBox(QMessageBox.Icon.Information, "션 업데이트", "", QMessageBox.StandardButton.NoButton)
    box._restart_offer = True
    now = box.addButton("지금 다시 시작", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("나중에", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(now)
    remaining = [seconds]
    timer = QTimer(box)

    def render():
        box.setText(f"새 버전 {version}을 준비했어요.\n"
                    f"{remaining[0]}초 뒤 션이 자동으로 다시 시작해 업데이트를 적용해요.\n"
                    "작성 중인 대화는 보관했다가 다시 시작한 뒤 복원해요.")

    def tick():
        remaining[0] -= 1
        if remaining[0] <= 0:
            timer.stop()
            box.done(int(QMessageBox.ButtonRole.AcceptRole.value))
            return
        render()

    def finished(_value):
        timer.stop()
        if box in dialogs:
            dialogs.remove(box)
        if box.clickedButton() is now or remaining[0] <= 0:
            restart()

    render()
    timer.timeout.connect(tick)
    box.finished.connect(finished)
    dialogs.append(box)
    timer.start(1000)
    box.open()
    return box


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
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            exe = str(INSTALL_ROOT / "pythonw.exe")
            cmd = f'"{exe}" "{INSTALL_ROOT / "main.py"}"'
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
    motion_preference = MotionPreference(app)
    settings = QSettings("SyeonPet", "PetPreferences")
    preferences = load_pet_preferences(settings, cfg)

    def save_preference(key, value):
        settings.setValue(key, value)
        settings.sync()

    sprites = SpriteBank(ASSETS)
    pet = PetWidget(sprites, display_size=int(cfg.get("display_size", 168)))
    pet.set_reduced_motion(effective_reduced_motion(
        motion_preference.reduced, preferences["follow_windows_motion"], preferences["reduced_motion"]))
    pet.BLEND_MS = float(cfg.get("frame_blend_ms", 0))
    pet.crossfade = preferences["blend"]
    if pet.crossfade and pet.BLEND_MS <= 0:
        pet.BLEND_MS = 30.0
    pet.motion_amp = float(cfg.get("motion_amplitude", 0.8))
    bubble = SpeechBubble(pet)
    pet.bubble_widget = bubble
    behavior = BehaviorController(pet)
    behavior.base_walk_speed = float(cfg.get("walk_speed", 190))
    behavior.WALK_SPEED = behavior.base_walk_speed
    behavior.click_actions.update(cfg.get("click_actions", {}))
    behavior.click_actions["double_click"] = preferences["double_click"]
    tour = OnboardingTour(pet, behavior, bubble, cfg.get("onboarding_steps", []))
    chat_panel = ChatPanel(pet, behavior, cfg)
    wardrobe_window = WardrobeWindow(pet)
    drafts = RestartDrafts(INSTALL_ROOT)
    try:
        restore_visible = drafts.restore(chat_panel)
    except Exception:
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
    if hasattr(chat_panel.work_widget, "busy_changed"):
        chat_panel.work_widget.busy_changed.connect(
            lambda busy: behavior.begin_thinking() if busy else behavior.end_thinking())
    pet.show()

    # Quick action pill toolbar floating near Syeon
    quick_bar = QuickActionBar()
    pet.clicked.connect(lambda: quick_bar.show_near_pet(pet.geometry(), pet.screen_bounds()))
    quick_bar.open_work.connect(lambda: chat_panel.show_section_near_pet("work"))
    quick_bar.open_finder.connect(lambda: chat_panel.show_section_near_pet("finder"))
    quick_bar.open_bag.connect(lambda: chat_panel.show_section_near_pet("bag"))
    quick_bar.open_wardrobe.connect(wardrobe_window.show_near_pet)

    ambient_lines = cfg.get("ambient_lines", [])
    care_catalog = cfg.get("care_lines", {})
    care_scheduler = OfficeCareScheduler()
    ambient_state = {"on": preferences["speech"]}
    ambient_until = 0.0

    def ambient_tick():
        nonlocal ambient_until
        if (not ambient_state["on"] or tour.active or pet._dragging or not bubble.isHidden()):
            return
        if time.monotonic() < ambient_until:
            return
        if behavior.mode == "idle" and random.random() < 0.40:
            if care_catalog:
                formatted = {k: [(ln, None) if isinstance(ln, str) else ln for ln in v] for k, v in care_catalog.items()}
                msg, motion = care_scheduler.pick_care_message(custom_care_lines=formatted)
                bubble.show_temporary(msg)
                if motion and behavior.can_play_extra_motion(motion):
                    behavior.play_extra_motion(motion)
            elif ambient_lines:
                bubble.show_temporary(random.choice(ambient_lines))
            ambient_until = time.monotonic() + 5.0

    ambient_timer = QTimer()
    ambient_timer.setInterval(20000)
    ambient_timer.timeout.connect(ambient_tick)
    ambient_timer.start()

    chat_panel.prompt_conversation.task_feedback.connect(
        lambda outcome: task_feedback(behavior, bubble, ambient_state["on"], outcome))
    if hasattr(chat_panel.work_widget, "task_feedback"):
        chat_panel.work_widget.task_feedback.connect(
            lambda outcome: task_feedback(behavior, bubble, ambient_state["on"], outcome))
    pet.petted.connect(lambda line: bubble.show_temporary(line, 3500) if ambient_state["on"] else None)
    chat_panel.companion_praise.connect(lambda msg: (behavior.play_one_shot("celebrate"), bubble.show_temporary(msg, 3500) if ambient_state["on"] else None))
    behavior.hub_requested.connect(lambda: chat_panel.show_section_near_pet("work"))

    focus_timer = FocusTimer(app)
    def on_timer_changed():
        active = bool(focus_timer.label)
        quick_bar.set_timer_active(active)
        pet.set_timer_badge(format_badge_text(focus_timer.label, focus_timer.remaining_seconds) if active else "")

    focus_timer.changed.connect(on_timer_changed)
    def on_focus_expired(label):
        c = focus_timer.cycle
        if c is not None and label == "집중":
            behavior.play_one_shot("celebrate")
            show_companion_message(behavior, bubble, ambient_state["on"],
                                   f"{c['done']}라운드 집중 완료! 잠깐 쉬면서 몸을 풀어요 🎉")
        elif c is not None:
            show_companion_message(behavior, bubble, ambient_state["on"],
                                   f"{label} 끝! 다시 집중을 시작해요 💪")
        else:
            show_companion_message(behavior, bubble, ambient_state["on"],
                                   f"{label} 시간이 끝났어요. 잠깐 몸을 풀어요!")

    focus_timer.expired.connect(on_focus_expired)
    quick_bar.toggle_timer.connect(lambda: focus_timer.cancel() if (focus_timer.label or focus_timer.completed_label) else focus_timer.start(25, "집중"))
    behavior.fully_asleep_changed.connect(lambda asleep: bubble.hide() if asleep else None)

    tray = QSystemTrayIcon(QIcon(str(ASSETS / "base-source.png")))
    tray.setToolTip("션 데스크탑 펫")
    menu = QMenu()
    update_checker = UpdatePreparer(INSTALL_ROOT, parent=app)
    update_dialogs: list = []

    def on_tray_activated(reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if pet._click_through:
                pet.restore_click_through()
                if ambient_state["on"]:
                    bubble.show_temporary("클릭 통과 모드를 해제했어요!", 3000)
            else:
                chat_panel.show_section_near_pet("work")

    tray.activated.connect(on_tray_activated)

    motion_notice = QLabel("Windows 동작 줄이기 적용 중")
    motion_notice.setObjectName("motionPreferenceStatus")
    motion_notice.setContentsMargins(8, 4, 8, 4)
    motion_status = QWidgetAction(menu)
    motion_status.setDefaultWidget(motion_notice)

    # Clean Top-Level Context Menu (<= 9 actions/submenus added directly to menu)
    menu.addAction(motion_status)
    act_work = menu.addAction("✨ 션 비서 열기 (초안 · 요약)")
    act_work.triggered.connect(lambda: chat_panel.show_section_near_pet("work"))
    act_finder = menu.addAction("🔍 내 파일 찾기 (폴더 탐정)")
    act_finder.triggered.connect(lambda: chat_panel.show_section_near_pet("finder"))
    act_bag = menu.addAction("🎒 가방 속 할 일 · 메모")
    act_bag.triggered.connect(lambda: chat_panel.show_section_near_pet("bag"))
    timer_menu = menu.addMenu("⏱️ 집중 · 휴식 타이머")
    build_timer_menu(timer_menu, focus_timer)
    act_wardrobe = menu.addAction("👗 션이 옷장")
    act_wardrobe.triggered.connect(wardrobe_window.show_near_pet)
    motion_menu = menu.addMenu("🎵 재롱 · 휴식")
    motion_actions = build_motion_menu(motion_menu, behavior, tour)

    def toggle_speech(checked):
        ambient_state["on"] = checked
        save_preference("speech", checked)
        if not checked and not tour.active:
            bubble.hide()
            pet._relayout()

    def set_frame_blending(enabled):
        if enabled and pet.BLEND_MS <= 0:
            pet.BLEND_MS = 30.0
        pet.crossfade = enabled
        pet._update_mask()
        pet.update()
        save_preference("blend", enabled)

    def set_quiet_mode(enabled):
        save_preference("reduced_motion", enabled)
        apply_motion_preference()

    def set_follow_windows_motion(enabled):
        save_preference("follow_windows_motion", enabled)
        apply_motion_preference()

    settings_menu = menu.addMenu("⚙️ 화면 · 설정 · 바로가기")
    settings_actions = build_settings_menu(
        settings_menu, pet, behavior, tour, chat_panel, update_checker,
        preferences, save_preference, ambient_state, toggle_speech,
        set_frame_blending, set_quiet_mode, set_follow_windows_motion, DOUBLE_CLICK_CHOICES,
        autostart_enabled, set_autostart)

    def quit_app(restart=False):
        try:
            pending = updater_switch.pending_version(INSTALL_ROOT)
        except Exception:
            box = QMessageBox(QMessageBox.Icon.Warning, "션 업데이트",
                              "업데이트 상태를 확인하지 못해 종료를 보류했어요. "
                              "작성 중인 내용을 보관하거나 복사한 뒤 다시 시도해 주세요.", QMessageBox.StandardButton.Ok)
            update_dialogs.append(box)
            box.finished.connect(lambda _v: update_dialogs.remove(box))
            box.open()
            return

        def finish_quit():
            try:
                prepare_quit(update_checker, drafts, chat_panel, INSTALL_ROOT)
            except Exception:
                box = QMessageBox(QMessageBox.Icon.Warning, "대화 보관 실패",
                                  "작성 중인 대화를 보관하지 못해 종료하지 않았어요.", QMessageBox.StandardButton.Ok)
                update_dialogs.append(box)
                box.finished.connect(lambda _v: update_dialogs.remove(box))
                box.open()
                return
            if restart:
                try:
                    updater_switch.restart_after_exit(INSTALL_ROOT)
                except Exception:
                    update_checker.cancel_shutdown()
                    box = QMessageBox(QMessageBox.Icon.Warning, "션 업데이트",
                                      "션을 다시 시작하지 못했어요. 션을 종료한 뒤 직접 다시 실행하면 "
                                      "업데이트가 적용됩니다.", QMessageBox.StandardButton.Ok)
                    update_dialogs.append(box)
                    box.finished.connect(lambda _v: update_dialogs.remove(box))
                    box.open()
                    return
            app.quit()
        chat_panel.request_app_quit(finish_quit, preserve_for_update=bool(pending))

    def on_update_result(result):
        if result.get("status") == "ready" and result.get("version"):
            offer_update_restart(result["version"], lambda: quit_app(restart=True), update_dialogs)
        else:
            present_update_result(result, tray, update_dialogs)

    update_checker.completed.connect(on_update_result)

    act_quit = menu.addAction("종료")
    act_quit.triggered.connect(lambda: quit_app())

    def apply_motion_preference(_sys_val=None):
        from_windows = motion_preference.reduced and settings_actions["follow_windows"].isChecked()
        enabled = effective_reduced_motion(
            motion_preference.reduced, settings_actions["follow_windows"].isChecked(),
            settings_actions["quiet"].isChecked())
        pet.set_reduced_motion(enabled)
        tour.refresh_motion_policy()
        wardrobe_window.set_reduced_motion(enabled)
        motion_notice.setText("Windows 동작 줄이기 적용 중" if from_windows else "고요 모드 · 프롬프트 작업은 계속할 수 있어요")
        motion_status.setText(motion_notice.text())
        motion_status.setVisible(enabled)
        for act in motion_actions.values():
            act.setEnabled(not enabled)
        settings_actions["blend"].setEnabled(not enabled)

    motion_preference.changed.connect(apply_motion_preference)
    apply_motion_preference()
    tray.setContextMenu(menu)
    bind_pet_menu(pet, menu)
    tray.show()

    QTimer.singleShot(2500, lambda: update_checker.check(manual=False))
    if _restore_error:
        QTimer.singleShot(1500, lambda: tray.showMessage("션 업데이트", "업데이트 또는 대화를 확인하지 못했어요."))
    elif os.environ.get("SYEONPET_UPDATE_ROLLBACK") == "1":
        QTimer.singleShot(1500, lambda: tray.showMessage("션 업데이트", "새 버전을 시작하지 못해 이전 버전으로 돌아왔어요."))
    if drafts.remaining_count():
        QTimer.singleShot(5000, lambda: tray.showMessage("션 대화 복원", "다른 창의 대화도 보관되어 있어요."))
    QTimer.singleShot(0, updater_switch.notify_ready)

    if cfg.get("start_onboarding_on_launch", False) and settings.value("onboarding_seen") is None:
        def start_first_tour():
            if not behavior.fully_asleep:
                save_preference("onboarding_seen", True)
                tour.start()
        QTimer.singleShot(1200, start_first_tour)

    exit_code = app.exec()
    motion_preference.changed.disconnect(apply_motion_preference)
    motion_preference.stop()
    motion_preference.deleteLater()
    if exit_code == 0:
        drafts.finish_cleanly()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
