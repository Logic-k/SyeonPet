"""Conversational prompt drafting, hosted inside the pet's existing window."""
from __future__ import annotations

import re
import sys
import threading

from PySide6.QtCore import QEvent, QObject, Signal, Qt
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

from optimizer_ai import optimize_ai
from optimizer_core import TASKS, create_offline_prompt, review_result, utf16_length, validate_input
from syeonpet.chat.dialogs import (
    CHAT_STYLE, PROVIDER_NOTICES, _ActionRow, _StatusText, build_confirm_dialog,
    open_clarification_dialog, open_comparison_dialog, open_library_dialog,
    open_template_dialog,
)
from syeonpet.chat.diff_view import compute_quality_text, open_diff_dialog
from syeonpet.chat.error_sanitizer import sanitize_error as _safe_generation_error
from syeonpet.chat.executor import (
    build_generation_input, check_over_limit, destination_label,
    handle_comparison_event, reload_profiles, resolve_profile, run_generate,
    snapshot_profiles, start_comparison, submit_confirmed,
)
from syeonpet.chat.state_codec import (
    _append_conditions, _is_paste_slot, _placeholders, apply_fill_to_result,
    draft_snapshot, export_update_state, fail_generation, finish_generation,
    get_open_template_snapshot, refresh_fill_panel, restore_update_state,
    toggle_fill_panel,
)
from syeonpet.chat.ui_builder import setup_chat_ui

_FIRST_REQUEST_EXAMPLE = "예: 연수 안내문 작성, 날짜는 [확인 필요]"
_FOLLOWUP_EXAMPLE = "예: 위 내용을 세 항목으로 나눠 주세요."
_PLACEHOLDER = re.compile(r"\[([^\[\]\n]{2,40})\]")
_MAX_FILL_FIELDS = 8
_CONDITION_LABELS = {"purpose": "목적", "audience": "대상", "format": "형식", "deadline": "기한"}
_ROLE_HEADING = "확정된 문의·답변 역할 (사용자 확인):"
_OTHER_AI = {"vireonix": ("pollinations-classic", "Pollinations Classic으로 보내기"),
             "pollinations-classic": ("vireonix", "Vireonix로 보내기"),
             "jarvisclaw": ("pollinations-classic", "Pollinations Classic으로 보내기")}
_SETTINGS_ERROR = ("AI 연결 설정이나 계정 정보를 읽지 못해 요청을 시작하지 않았어요. "
                   "잠시 후 다시 시도하고, 계속되면 AI 설정을 확인해 주세요.")


class _ReplySignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class PromptConversation(QWidget):
    busy_changed = Signal(bool)
    notice_raised = Signal()
    result_ready = Signal()
    task_feedback = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.font_scale = 10
        self.original, self.revisions, self.results = "", [], []
        self._result_revision_counts, self._result_review_inputs = [], []
        self._pending_review_input, self._shown_result = None, -1
        self._busy, self._sleep_paused, self._consent_epoch = False, False, 0
        self._clarifications, self._comparison_active = "", False
        self._comparison_signals = _ReplySignals(self)
        self._comparison_signals.completed.connect(self._comparison_event)
        self._signals = _ReplySignals(self)
        self._signals.completed.connect(self._finish)
        self._signals.failed.connect(self._fail)
        setup_chat_ui(self)

    def set_font_scale(self, pt: int) -> None:
        self.font_scale = int(pt)
        font = self.font()
        font.setPointSize(int(pt))
        self.setFont(font)
        for w in (self.input, self.result, self.conversation, self.status):
            w.setFont(font)

    def eventFilter(self, watched, event):
        if (watched is self.input and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() in (Qt.KeyboardModifier.ControlModifier,
                                          Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.KeypadModifier)):
            if not event.isAutoRepeat():
                self.submit()
            return True
        return super().eventFilter(watched, event)

    def _update_input_example(self):
        ex = _FOLLOWUP_EXAMPLE if self.original else _FIRST_REQUEST_EXAMPLE
        self.input.setPlaceholderText(ex)
        self.input.setAccessibleDescription(ex)

    def _toggle_history(self, visible):
        self.conversation.setVisible(visible)
        self.conversation_label.setText("션과 대화" if visible else "션과 프롬프트 정리")
        self.history_toggle.setText("대화 기록 접기" if visible else "대화 기록 보기")
        self.history_toggle.setAccessibleName("대화 기록 접기" if visible else "대화 기록 표시")

    def _toggle_tools(self, visible):
        if not visible and any(t.hasFocus() for t in (self.clarify, self.compare, self.diff)):
            self.tools_toggle.setFocus()
        for t in (self.clarify, self.compare, self.diff):
            t.setVisible(visible)
        self.tools_toggle.setText("도구 접기" if visible else "도구 더보기")
        self.tools_toggle.setAccessibleName("추가 도구 접기" if visible else "추가 도구 표시")

    def _show_notice(self, text):
        self.inline_notice.setText(text)
        self.inline_notice.show()
        self.notice_raised.emit()

    def _provider_changed(self):
        self._consent_epoch += 1
        self._hide_other_ai()
        p = self.provider.currentData()
        snd = "선택한 외부 서비스로 원문과 추가 조건, 이전 결과가 전송됩니다. 전송 전 다시 확인합니다."
        if p == "offline":
            self.provider_note.setText("입력한 문장을 그대로 구조화합니다. 자연스러운 재작성은 AI 방식을 선택해 주세요.")
        elif p == "pollinations-classic":
            self.provider_note.setText("Vireonix가 응답하지 않을 때 쓰는 예비 서비스예요. 응답에 수십 초가 걸릴 수 있고, 보낸 뒤 30초가 지나야 다시 보낼 수 있어요. " + snd)
        elif p == "jarvisclaw":
            self.provider_note.setText("로그인과 키 없이 외부 무료 AI를 사용합니다. 가용성과 모델이 바뀔 수 있습니다. 개인정보·비공개 자료는 보내지 마세요. " + snd)
        elif p == "kilo":
            self.provider_note.setText("로그인과 키 없이 Kilo의 무료 모델을 사용합니다. 무료 경로는 요청이 기록되어 서비스 개선에 쓰일 수 있으니 "
                                       "개인정보·비공개 자료는 보내지 마세요. 같은 인터넷 연결에서 시간당 200회까지입니다. " + snd)
        elif p == "ovhcloud":
            self.provider_note.setText("로그인과 키 없이 OVHcloud(유럽)의 gpt-oss-120b를 사용합니다. 같은 인터넷 연결에서 분당 2회까지라 "
                                       "보낸 뒤 30초가 지나야 다시 보낼 수 있어요. 개인정보·비공개 자료는 보내지 마세요. " + snd)
        elif str(p).startswith("profile:"):
            try:
                prof = self._resolve_profile(p)
                if prof.provider == "ollama":
                    self.provider_note.setText("선택한 Ollama 로컬 서버에서 처리합니다. 서버의 클라우드 비활성화 설정을 확인해 주세요.")
                elif prof.provider == "chatgpt-subscription":
                    self.provider_note.setText("연결한 ChatGPT 계정의 구독 한도를 사용합니다. 전송 전 내용을 다시 확인합니다.")
                else:
                    self.provider_note.setText(snd)
            except ValueError as exc:
                self.provider_note.setText(str(exc))
        else:
            self.provider_note.setText(snd)

    def reload_profiles(self):
        reload_profiles(self)

    def _resolve_profile(self, destination):
        return resolve_profile(destination)

    def _destination(self, destination):
        return destination_label(destination, self)

    def _profile_snapshot(self):
        return snapshot_profiles()

    def _open_comparison(self):
        open_comparison_dialog(self)

    def _generation_input(self, text):
        return build_generation_input(self, text)

    def _over_limit(self, original, revision, context, previous, provider):
        return check_over_limit(original, revision, context, previous, provider)

    @staticmethod
    def _generate(frozen, destination, profile=None):
        return run_generate(frozen, destination, profile)

    def _start_comparison(self, expected, frozen, destinations, profiles):
        start_comparison(self, expected, frozen, destinations, profiles)

    def _comparison_event(self, event):
        handle_comparison_event(self, event)

    def _open_ai_settings(self):
        try:
            from ai_settings import AISettingsDialog
            dlg = AISettingsDialog(self)
            self._settings_dialog = dlg
            dlg.finished.connect(self.reload_profiles)
            dlg.open()
        except Exception:
            self._show_notice(_SETTINGS_ERROR)

    def _open_clarification(self):
        open_clarification_dialog(self)

    def _quality_text(self):
        return compute_quality_text(self)

    def _open_diff(self):
        open_diff_dialog(self)

    def _refresh_quality(self):
        view = getattr(self, "_diff_view", None)
        if view is not None:
            view.setPlainText(self._quality_text())

    def _hide_other_ai(self):
        self.retry_other.hide()
        self._other_ai_target = None

    def _send_to_other_ai(self):
        if self._other_ai_target:
            self.provider.setCurrentIndex(self.provider.findData(self._other_ai_target))
            self.submit()

    def _refresh_fill(self):
        refresh_fill_panel(self)

    def _toggle_fill(self, visible):
        toggle_fill_panel(self, visible)

    def _apply_fill(self):
        apply_fill_to_result(self)

    def _insert_template(self):
        open_template_dialog(self)

    def _open_library(self):
        open_library_dialog(self)

    def _restore_library(self, item):
        if self._busy:
            self.status.setText("정리 중에는 보관함을 불러올 수 없어요. 기존 입력은 그대로 남아 있습니다.")
            self._show_notice("정리 중에는 보관함을 불러올 수 없어요. 입력은 그대로 남아 있습니다.")
            return
        cur = self._draft_snapshot()
        if cur != getattr(self, "_library_snapshot", cur):
            self._confirm("변경된 입력 확인", "보관함을 연 뒤 대화나 입력이 바뀌었어요. 현재 내용을 바꾸고 불러올까요?",
                          lambda: self._apply_library_if_unchanged(item, cur))
            return
        self._apply_library(item)

    def _apply_library_if_unchanged(self, item, expected):
        if self._busy or self._draft_snapshot() != expected:
            self.status.setText("입력이 다시 바뀌어 불러오지 않았어요. 기존 내용은 그대로 남아 있습니다.")
            self._show_notice("입력이 바뀌어 불러오지 않았어요. 기존 내용은 그대로 남아 있습니다.")
            return
        self._apply_library(item)

    def _apply_library(self, item):
        self._reset_copy()
        self._clarifications = ""
        self.original = item.get("prompt", "")
        self._update_input_example()
        self.task.setCurrentIndex(max(0, self.task.findData(item.get("task", "auto"))))
        self.detail.setCurrentIndex(max(0, self.detail.findData(item.get("detail", "balanced"))))
        self.revisions = list(item.get("syeonRevisions", []))
        self.results = [item.get("result", "")]
        self._result_revision_counts = [len(self.revisions)]
        self._result_review_inputs = [None]
        self._shown_result = -1
        self.result_choice.clear()
        self.result_choice.addItem("보관함에서 복원한 결과")
        self._select_result(0)
        self.conversation.setPlainText("션: 보관한 요청과 결과를 불러왔어요. 추가 조건을 말씀해 주세요.\n\n나: " + self.original +
                                       "".join("\n\n나: " + rev for rev in self.revisions))
        self.input.clear()
        self.status.setText("복원한 원문과 결과를 확인해 주세요.")

    def _save_result(self):
        if 0 <= self._shown_result < len(self.results):
            self.results[self._shown_result] = self.result.toPlainText()

    def _select_result(self, index):
        self._reset_copy()
        self._save_result()
        self._shown_result = index
        self.result.setPlainText(self.results[index] if 0 <= index < len(self.results) else "")
        self._refresh_metadata()
        if not self._busy and 0 <= index < len(self.results):
            rev = self._selected_review()
            self.status.setText("\n".join(rev) if rev else "선택한 결과의 원문과 조건을 확인해 주세요.")

    def _selected_review(self):
        idx = self._shown_result
        if 0 <= idx < len(self._result_review_inputs):
            src = self._result_review_inputs[idx]
            if src is not None:
                return review_result(src[0], src[1], self.result.toPlainText())
        return []

    def _refresh_metadata(self):
        meta = self.result_choice.itemData(self._shown_result)
        if meta and isinstance(meta, dict):
            el = meta.get("elapsed", 0)
            self.result_metadata.setText(f'{meta.get("provider", "")} / {meta.get("model", "")} · {el:.2f}초')
        else:
            self.result_metadata.clear()

    def _reset_copy(self):
        self.copy.setText("결과 복사")

    def _copy_result(self):
        val = self.result.toPlainText()
        if not val:
            self.status.setText("복사할 결과가 없어요. 먼저 정리를 하거나 결과를 직접 작성해 주세요.")
            return
        self._reset_copy()
        cb = QApplication.clipboard()
        try:
            cb.setText(val)
            copied = cb.text().replace("\r\n", "\n") == val.replace("\r\n", "\n")
        except RuntimeError:
            copied = False
        if not copied:
            self.status.setText("복사하지 못했어요. 다시 시도해 주세요.")
            return
        self.copy.setText("복사됨")
        rem = len(_placeholders(val))
        self.status.setText("결과를 복사했어요. 원문과 조건을 확인한 뒤 사용해 주세요."
                            + (f" 아직 채우지 않은 [대괄호] 칸이 {rem}개 있어요." if rem else ""))
        self.task_feedback.emit("copied")

    def _new_request(self):
        if self._busy:
            return
        if self.original or self.input.toPlainText() or self.result.toPlainText():
            exp = self._draft_snapshot()
            self._confirm("새 요청", "현재 대화와 결과를 지우고 새 요청을 시작할까요?\n필요한 내용은 먼저 복사해 주세요.",
                          lambda: self._clear_if_unchanged(exp))
            return
        self._clear_request()

    def _draft_snapshot(self):
        return draft_snapshot(self)

    def _clear_if_unchanged(self, expected):
        if self._busy or self._draft_snapshot() != expected:
            self.status.setText("확인 중 입력이 바뀌어 새 요청을 시작하지 않았어요. 기존 내용은 그대로 남아 있습니다.")
            self._show_notice("입력이 바뀌어 새 요청을 시작하지 않았어요. 기존 내용은 그대로 남아 있습니다.")
            return
        self._clear_request()

    def _clear_request(self):
        self._reset_copy()
        self.original = ""
        self._clarifications = ""
        self._update_input_example()
        self.revisions.clear()
        self.results.clear()
        self._result_revision_counts.clear()
        self._result_review_inputs.clear()
        self._pending_review_input = None
        self._shown_result = -1
        self.result_choice.clear()
        self.result.clear()
        self.input.clear()
        self.inline_notice.hide()
        self._hide_other_ai()
        self.conversation.setPlainText("션: 어떤 요청을 정리할까요?")
        self.status.setText("새 요청을 입력해 주세요.")

    def _set_busy(self, busy):
        busy = bool(busy)
        changed = busy != self._busy
        self._busy = busy
        for c in (self.input, self.send, self.provider, self.task, self.detail, self.template, self.library,
                  self.reset, self.result, self.result_choice, self.fill_toggle, self.fill_panel,
                  self.ai_settings, self.clarify, self.compare):
            c.setEnabled(not busy)
        self.send.setEnabled(not busy and not self._sleep_paused)
        self.retry_other.setEnabled(not busy and not self._sleep_paused)
        if changed:
            self.busy_changed.emit(busy)

    @property
    def is_busy(self) -> bool:
        return self._busy

    def has_pending_work(self) -> bool:
        return bool(self.original or self.revisions or self.results or self._clarifications
                    or self.input.toPlainText() or self.result.toPlainText()
                    or self._open_template_snapshot() is not None)

    def _open_template_snapshot(self):
        return get_open_template_snapshot(self)

    def quit_snapshot(self):
        return self._request_snapshot(), self._open_template_snapshot()

    def export_update_state(self):
        return export_update_state(self)

    def restore_update_state(self, state):
        restore_update_state(self, state)

    def set_sleep_paused(self, paused: bool):
        self._sleep_paused = bool(paused)
        self._consent_epoch += 1
        self.send.setEnabled(not self._busy and not self._sleep_paused)
        self.retry_other.setEnabled(not self._busy and not self._sleep_paused)

    def _confirm(self, title, text, callback):
        self._confirmation = build_confirm_dialog(self, title, text, callback)
        self._confirmation.open()
        return self._confirmation

    def submit(self):
        if self._busy or self._sleep_paused:
            return
        text = self.input.toPlainText()
        if not text.strip():
            self.status.setText("먼저 요청 또는 추가 조건을 입력해 주세요.")
            self._show_notice("먼저 요청 또는 추가 조건을 입력해 주세요.")
            return
        provider = self.provider.currentData()
        try:
            profile = self._resolve_profile(provider)
        except ValueError as exc:
            self._show_notice(str(exc))
            return
        task, detail = self.task.currentData(), self.detail.currentData()
        original = self.original or text
        previous = self.result.toPlainText() if self.original else ""
        old_ctx = "\n\n".join(filter(None, [self._clarifications, *self.revisions]))
        revision = text if self.original else ""
        next_ctx = "\n\n".join(filter(None, [old_ctx, revision]))
        cnt = (self._result_revision_counts[self._shown_result]
               if 0 <= self._shown_result < len(self._result_revision_counts) else 0)
        pending = [*self.revisions[cnt:], revision] if revision else []
        over = self._over_limit(original, revision, old_ctx if provider != "offline" else next_ctx, previous, provider)
        if over:
            self._show_notice(over)
            return
        if provider != "offline":
            service = self._destination(provider)
            info = (f"원래 요청, 추가 조건과 이전 결과를 {service}에 전송합니다. "
                    "제공자는 내용을 저장하거나 서비스 개선에 사용할 수 있습니다. "
                    "학생·학부모·직원 식별정보, 민원 원문과 비공개 문서는 제외하거나 "
                    "가상 내용으로 바꾸고 기관의 사용 정책과 허용 서비스를 확인해 주세요.\n\n전송하시겠어요?")
            if provider in PROVIDER_NOTICES:
                info = (f"원래 요청, 추가 조건과 이전 결과를 {service}에 전송합니다.\n\n"
                        f"{PROVIDER_NOTICES[provider]}\n\n전송하시겠어요?")
            if profile is not None and profile.provider == "ollama":
                info = f"{service} 로컬 서버에서 원래 요청, 추가 조건과 이전 결과를 처리합니다. 로컬 처리를 시작할까요?"
            try:
                expected = self._request_snapshot()
            except Exception:
                self._show_notice("연결 설정을 읽지 못했어요. AI 연결 설정을 다시 확인해 주세요.")
                return
            dialog = self._confirm("로컬 AI 처리 확인" if profile is not None and profile.provider == "ollama" else "외부 AI 전송 확인",
                                  info, lambda: self._submit_if_unchanged(expected, text, original, revision, provider, old_ctx, next_ctx, previous, task, detail, pending, profile))
            if isinstance(dialog, QMessageBox):
                dialog.setTextFormat(Qt.TextFormat.PlainText)
                dialog.setDetailedText(
                    f"서비스: {service}\n작업 유형: {self.task.currentText()} ({task})\n"
                    f"상세 수준: {self.detail.currentText()} ({detail})\n\n"
                    f"원래 요청:\n{original}\n\n누적 조건:\n{old_ctx}\n\n"
                    f"이번 추가 조건:\n{revision}\n\n선택한 이전 결과 (편집 반영):\n{previous}\n\n"
                    "위 내용은 이번 요청의 입력값입니다. 앱 지시문과 모델/요청 메타데이터도 동반하며 전체 전송 원문은 아닙니다.")
                dialog.button(QMessageBox.StandardButton.Yes).setText(f"{service}로 전송")
                cancel = dialog.button(QMessageBox.StandardButton.No)
                cancel.setText("취소")
                cancel.setFocus()
                dialog.finished.connect(lambda _: dialog.setDetailedText(""))
                dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            return
        self._submit_confirmed(text, original, revision, provider, old_ctx, next_ctx, previous, task, detail, pending)

    def _request_snapshot(self):
        return (self._draft_snapshot(), self.provider.currentData(), self.task.currentData(),
                self.detail.currentData(), self._clarifications, self._consent_epoch,
                self._profile_snapshot())

    def _submit_if_unchanged(self, expected, *args):
        if self._request_snapshot() != expected:
            self.status.setText("확인 중 입력이나 설정이 바뀌어 전송하지 않았어요. 내용을 다시 확인해 주세요.")
            self._show_notice("입력이나 설정이 바뀌어 전송하지 않았어요. 다시 확인해 주세요.")
            return
        self._submit_confirmed(*args)

    def _submit_confirmed(self, text, original, revision, provider, old_ctx, next_ctx, previous, task, detail, pending, profile=None):
        submit_confirmed(self, text, original, revision, provider, old_ctx, next_ctx, previous, task, detail, pending, profile)

    def _finish(self, reply):
        finish_generation(self, reply)

    def _fail(self, message):
        fail_generation(self, message)
