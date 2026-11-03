"""Submenu builder and action organizer for SyeonPet tray and pet context menus."""
from __future__ import annotations

import sys
from typing import Callable

from PySide6.QtCore import QObject, SIGNAL
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QMessageBox

from syeonpet.ui.shortcut_helper import create_desktop_shortcut


def add_focus_timer_menu(parent: QMenu, timer) -> QMenu:
    """Add standard focus/rest timer submenu to parent and return it."""
    timer_menu = parent.addMenu("집중 / 휴식 타이머")
    status = timer_menu.addAction("실행 중인 타이머 없음")
    status.setEnabled(False)
    timer_menu.addAction("25분 집중").triggered.connect(lambda: timer.start(25, "집중"))
    timer_menu.addAction("5분 휴식").triggered.connect(lambda: timer.start(5, "휴식"))
    cancel = timer_menu.addAction("타이머 취소")
    cancel.triggered.connect(timer.cancel)

    def refresh():
        seconds = timer.remaining_seconds
        if timer.label:
            status.setText(f"{timer.label} · {seconds // 60:02d}:{seconds % 60:02d} 남음")
        elif timer.completed_label:
            status.setText(f"{timer.completed_label} 완료 · 다음 타이머를 시작할 수 있어요")
        else:
            status.setText("실행 중인 타이머 없음")
        cancel.setText("완료 기록 지우기" if timer.completed_label else "타이머 취소")
        cancel.setEnabled(bool(timer.label or timer.completed_label))

    QObject.connect(timer, SIGNAL("changed()"), timer_menu, refresh)
    timer_menu.aboutToShow.connect(refresh)
    refresh()
    return timer_menu


def build_timer_menu(timer_menu: QMenu, timer) -> tuple[QAction, QAction]:
    """Populate Pomodoro focus and rest timer actions."""
    status = timer_menu.addAction("실행 중인 타이머 없음")
    status.setEnabled(False)

    for mins, label in ((10, "10분 집중"), (25, "25분 집중"), (50, "50분 집중"), (5, "5분 휴식")):
        act = timer_menu.addAction(label)
        mode = "휴식" if mins == 5 else "집중"
        act.triggered.connect(lambda _=False, m=mins, lbl=mode: timer.start(m, lbl))

    cancel = timer_menu.addAction("타이머 취소")
    cancel.triggered.connect(timer.cancel)

    def refresh():
        seconds = timer.remaining_seconds
        if timer.label:
            status.setText(f"{timer.label} · {seconds // 60:02d}:{seconds % 60:02d} 남음")
        elif timer.completed_label:
            status.setText(f"{timer.completed_label} 완료 · 다음 타이머를 시작할 수 있어요")
        else:
            status.setText("실행 중인 타이머 없음")
        cancel.setText("완료 기록 지우기" if timer.completed_label else "타이머 취소")
        cancel.setEnabled(bool(timer.label or timer.completed_label))

    QObject.connect(timer, SIGNAL("changed()"), timer_menu, refresh)
    timer_menu.aboutToShow.connect(refresh)
    refresh()
    return status, cancel


def build_motion_menu(
    motion_menu: QMenu,
    behavior,
    tour,
) -> dict[str, QAction]:
    """Populate play, sleep, and cute companion motion actions."""
    actions: dict[str, QAction] = {}

    act_wander = motion_menu.addAction("돌아다니기")
    act_wander.triggered.connect(behavior.wander)
    actions["wander"] = act_wander

    act_celebrate = motion_menu.addAction("축하하기")
    act_celebrate.triggered.connect(lambda: behavior.play_one_shot("celebrate"))
    actions["celebrate"] = act_celebrate

    act_dance = motion_menu.addAction("춤추기")
    act_dance.triggered.connect(lambda: behavior.dance(4.8))
    actions["dance"] = act_dance

    act_jump = motion_menu.addAction("점프하기")
    act_jump.triggered.connect(lambda: behavior.play_one_shot("jump"))
    actions["jump"] = act_jump

    for label, state in (("기지개", "stretch"), ("꾸벅 인사", "bow")):
        act = motion_menu.addAction(label)
        act.triggered.connect(lambda _=False, s=state: behavior.play_extra_motion(s))
        actions[state] = act

    motion_menu.addSeparator()

    act_sleep = motion_menu.addAction("재우기 / 깨우기")
    act_sleep.triggered.connect(behavior.toggle_sleep)
    actions["sleep"] = act_sleep

    act_liesleep = motion_menu.addAction("누워서 재우기")
    act_liesleep.triggered.connect(behavior.deep_sleep)
    actions["liesleep"] = act_liesleep

    def sleep_completely():
        if tour.active:
            tour.finish()
        behavior.still_sleep()

    act_still = motion_menu.addAction("완전히 가만히 재우기")
    act_still.triggered.connect(sleep_completely)
    actions["still"] = act_still

    def refresh_extra():
        for state in ("stretch", "bow"):
            if state in actions:
                actions[state].setEnabled(behavior.can_play_extra_motion(state))

    motion_menu.aboutToShow.connect(refresh_extra)
    return actions


def build_settings_menu(
    settings_menu: QMenu,
    pet,
    behavior,
    tour,
    chat_panel,
    update_checker,
    preferences: dict,
    save_pref: Callable[[str, object], None],
    ambient_state: dict,
    toggle_speech: Callable[[bool], None],
    set_frame_blending: Callable[[bool], None],
    set_quiet_mode: Callable[[bool], None],
    double_click_choices: tuple[tuple[str, str], ...],
    autostart_enabled_fn: Callable[[], bool],
    set_autostart_fn: Callable[[bool], None],
) -> dict[str, QAction]:
    """Populate settings, shortcuts, accessibility toggles, and updates."""
    actions: dict[str, QAction] = {}

    def make_desktop_shortcut():
        try:
            path = create_desktop_shortcut()
            QMessageBox.information(None, "바탕화면 바로가기", f"바탕화면에 바로가기를 만들었어요!\n{path.name}")
        except Exception as exc:
            QMessageBox.warning(None, "바로가기 만들기 실패", f"바로가기를 만들지 못했습니다: {exc}")

    act_shortcut = settings_menu.addAction("바탕화면에 션 바로가기 만들기")
    act_shortcut.triggered.connect(make_desktop_shortcut)
    actions["shortcut"] = act_shortcut

    act_prompt = settings_menu.addAction("프롬프트 정리 열기")
    act_prompt.triggered.connect(chat_panel.show_prompt_near_pet)
    actions["prompt"] = act_prompt

    def toggle_tour():
        if tour.active:
            tour.finish()
        else:
            behavior.wake()
            tour.start()

    act_tour = settings_menu.addAction("사용 안내 보기")
    act_tour.triggered.connect(toggle_tour)
    actions["tour"] = act_tour

    settings_menu.addSeparator()

    act_calm = settings_menu.addAction("차분 모드 (천천히)")
    act_calm.setCheckable(True)
    act_calm.setChecked(preferences["calm"])
    behavior.set_slow(act_calm.isChecked())
    act_calm.triggered.connect(behavior.set_slow)
    act_calm.triggered.connect(lambda v: save_pref("calm", v))
    actions["calm"] = act_calm

    act_quiet = settings_menu.addAction("고요 모드 (작업 계속)")
    act_quiet.setCheckable(True)
    act_quiet.setChecked(preferences["reduced_motion"])
    act_quiet.triggered.connect(set_quiet_mode)
    actions["quiet"] = act_quiet

    act_speech = settings_menu.addAction("말풍선 표시")
    act_speech.setCheckable(True)
    act_speech.setChecked(ambient_state["on"])
    act_speech.triggered.connect(toggle_speech)
    actions["speech"] = act_speech

    act_blend = settings_menu.addAction("프레임 부드럽게 전환")
    act_blend.setCheckable(True)
    act_blend.setChecked(pet.crossfade)
    act_blend.triggered.connect(set_frame_blending)
    actions["blend"] = act_blend

    # Double click action choice submenu
    dbl_menu = settings_menu.addMenu("더블클릭 동작")
    dbl_group = QActionGroup(dbl_menu)
    cur_dbl = behavior.click_actions.get("double_click", "open_hub")

    def set_double_click(name: str):
        behavior.click_actions["double_click"] = name
        save_pref("double_click", name)

    for label, name in double_click_choices:
        a = QAction(label, dbl_menu, checkable=True)
        a.setChecked(name == cur_dbl)
        a.triggered.connect(lambda _c=False, n=name: set_double_click(n))
        dbl_group.addAction(a)
        dbl_menu.addAction(a)

    settings_menu.addSeparator()

    act_through = settings_menu.addAction("클릭 통과 모드 (Ctrl+Alt+S 해제)")
    act_through.setCheckable(True)
    act_through.setChecked(pet._click_through)
    act_through.triggered.connect(pet.set_click_through)
    pet.click_through_changed.connect(act_through.setChecked)
    actions["through"] = act_through

    if sys.platform == "win32":
        act_auto = settings_menu.addAction("Windows 시작 시 자동 실행")
        act_auto.setCheckable(True)
        act_auto.setChecked(autostart_enabled_fn())
        act_auto.triggered.connect(set_autostart_fn)
        actions["autostart"] = act_auto

    settings_menu.addSeparator()

    act_update = settings_menu.addAction("업데이트 확인")
    act_update.triggered.connect(lambda: update_checker.check(manual=True))
    actions["update"] = act_update

    return actions
