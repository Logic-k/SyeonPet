"""Exercise the in-pet conversation without a network or user data."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QEventLoop, QRect, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QPalette, QTextCursor
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox, QPushButton

from optimizer_chat import PromptConversation
from optimizer_library import LibraryDialog, LibraryStore


class PromptConversationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._profile_patch = patch("ai_profiles.load_profiles", return_value=[])
        self._profile_patch.start()
        self.addCleanup(self._profile_patch.stop)
        self.panel = PromptConversation()
        # Subscribe before any action can start a worker. The nested Qt loop
        # delivers queued GUI completion/error signals, with a bounded timeout.
        self._completion_loop = QEventLoop()
        self._completion_timer = QTimer()
        self._completion_timer.setSingleShot(True)
        self._completion_timer.timeout.connect(self._completion_loop.quit)
        self.panel.busy_changed.connect(lambda busy: None if busy else self._completion_loop.quit())
        self.panel.show()
        self.app.processEvents()

    def tearDown(self):
        self._wait_idle()
        self.panel.close()
        self.app.processEvents()

    def _wait_idle(self):
        if self.panel._busy:
            self._completion_timer.start(5000)
            self._completion_loop.exec()
            self._completion_timer.stop()
        self.app.processEvents()
        self.assertFalse(self.panel._busy, "worker did not complete")

    def _submit_offline(self, text):
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("offline"))
        self.panel.input.setPlainText(text)
        self.panel.submit()
        self._wait_idle()

    def _key(self, widget, key, modifiers=Qt.KeyboardModifier.NoModifier, text=""):
        QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text))
        QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyRelease, key, modifiers, text))

    def test_optional_clarification_preserves_raw_original_and_applies_only_answers(self):
        raw = "QA_REQUEST"
        self.panel.input.setPlainText(raw)
        self.panel.clarify.click()
        self.assertEqual(set(self.panel._clarification_fields),
                         {"purpose", "audience", "format", "deadline"})
        self.panel._clarification_fields["audience"].setText("QA_AUDIENCE")
        self.panel._clarification_dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Apply).click()
        self.assertEqual(self.panel.input.toPlainText(), raw)
        with patch("optimizer_chat.create_offline_prompt",
                   return_value={"result": "QA_RESULT", "notes": []}) as generate:
            self._submit_offline(raw)
        self.assertEqual(self.panel.original, raw)
        self.assertIn("QA_AUDIENCE", generate.call_args.kwargs["context"])
        self.panel._clear_request()
        self.panel.input.setPlainText(raw)
        self.panel.clarify.click()
        self.panel._clarification_fields["purpose"].setText("QA_NOT_APPLIED")
        self.panel._clarification_dialog.reject()
        self.assertEqual(self.panel._clarifications, "")

    def test_optional_inquiry_roles_reach_request_and_review_without_changing_original(self):
        raw = "가상 학교가 교육청에 문의한 내용의 회신 초안을 요청해 줘."
        roles = "문의자: 가상 학교 / 답변자: 교육청 / 수신자: 가상 학교"
        self.panel.input.setPlainText(raw)
        self.panel.clarify.click()
        field = self.panel._clarification_fields["inquiry_roles"]
        self.assertIn("선택", field.accessibleName())
        field.setText(roles)
        self.panel._clarification_dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Apply).click()
        self.assertEqual(self.panel.input.toPlainText(), raw)
        self.assertEqual(self.panel._clarifications, "inquiry_roles: " + roles)
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("jarvisclaw"))
        with patch("optimizer_chat.optimize_ai", return_value={"result": "회신 초안", "notes": []}) as generate:
            self.panel.submit()
            generate.assert_not_called()
            self.panel._confirmation.button(QMessageBox.StandardButton.Yes).click()
            self._wait_idle()
        self.assertEqual(generate.call_args.args[0], raw)
        self.assertEqual(generate.call_args.kwargs["context"], "inquiry_roles: " + roles)
        self.assertIn("문의한 사람이나 기관", self.panel.status.text())
        self.assertEqual(self.panel.original, raw)
        self.panel.clarify.click()
        self.assertNotIn("inquiry_roles", self.panel._clarification_fields)
        self.panel._clarification_dialog.reject()

    def test_empty_or_cancelled_inquiry_roles_do_not_create_conditions(self):
        raw = "가상 학교의 문의에 대한 회신 초안을 준비해 줘."
        self.panel.input.setPlainText(raw)
        self.panel.clarify.click()
        self.assertIn("inquiry_roles", self.panel._clarification_fields)
        self.panel._clarification_dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Apply).click()
        self.assertEqual(self.panel._clarifications, "")
        self.assertEqual(self.panel.input.toPlainText(), raw)
        self.panel.clarify.click()
        self.panel._clarification_fields["inquiry_roles"].setText("미정")
        self.panel._clarification_dialog.reject()
        self.assertEqual(self.panel._clarifications, "")
        self.assertEqual(self.panel.input.toPlainText(), raw)

    def test_profile_revision_or_model_change_invalidates_selected_consent(self):
        from dataclasses import replace
        from ai_profiles import AIProfile
        profile = AIProfile("qa", "groq", "qa-model", free_tier_acknowledged=True)
        profiles = [profile]
        with patch("ai_profiles.load_profiles", side_effect=lambda: list(profiles)), \
                patch("ai_secrets.get_secret", return_value={"api_key": "QA_SECRET"}), \
                patch("optimizer_chat.optimize_ai") as generate:
            self.panel.reload_profiles()
            self.panel.provider.setCurrentIndex(self.panel.provider.findData("profile:qa"))
            self.panel.input.setPlainText("QA_INPUT")
            self.panel.submit()
            self.assertIn("qa-model", self.panel._confirmation.text())
            profiles[0] = replace(profile, model="qa-other", revision=1)
            self.panel._confirmation.button(QMessageBox.StandardButton.Yes).click()
            self.assertFalse(self.panel.is_busy)
            generate.assert_not_called()
            self.assertEqual(self.panel.input.toPlainText(), "QA_INPUT")

    def test_profile_selected_call_uses_frozen_profile_and_metadata(self):
        from ai_profiles import AIProfile
        profile = AIProfile("qa", "groq", "qa-model", free_tier_acknowledged=True)
        with patch("ai_profiles.load_profiles", return_value=[profile]), \
                patch("ai_secrets.get_secret", return_value={"api_key": "QA_SECRET"}), \
                patch("optimizer_chat.optimize_ai",
                      return_value={"result": "QA_RESULT", "notes": []}) as generate:
            self.panel.reload_profiles()
            self.panel.provider.setCurrentIndex(self.panel.provider.findData("profile:qa"))
            self.panel.input.setPlainText("QA_INPUT")
            self.panel.submit()
            generate.assert_not_called()
            self.panel._confirmation.button(QMessageBox.StandardButton.Yes).click()
            self._wait_idle()
            self.assertEqual(generate.call_args.kwargs["profile"], profile)
            self.assertEqual(generate.call_args.kwargs["provider"], profile.provider)
            metadata = self.panel.result_choice.currentData()
            self.assertEqual((metadata["provider"], metadata["model"]), ("groq", "qa-model"))
            self.assertGreaterEqual(metadata["elapsed"], 0)
            self.assertNotIn("QA_SECRET", repr(self.panel.export_update_state()))

    def test_credential_revision_and_sleep_wake_invalidate_prior_consent(self):
        from dataclasses import replace
        from ai_profiles import AIProfile
        from chatgpt_auth import AccountInfo
        profile = AIProfile("qa", "chatgpt-subscription", "qa-model", account_id="qa-account")
        account = AccountInfo("qa-account", "QA", "QA", True, 1)
        with patch("ai_profiles.load_profiles", return_value=[profile]), \
                patch("chatgpt_auth.account_status", return_value=account) as status, \
                patch("optimizer_chat.optimize_ai") as generate:
            self.panel.reload_profiles()
            self.panel.provider.setCurrentIndex(self.panel.provider.findData("profile:qa"))
            self.panel.input.setPlainText("QA_INPUT")
            self.panel.submit()
            status.return_value = replace(account, credential_revision=2)
            self.panel._confirmation.button(QMessageBox.StandardButton.Yes).click()
            generate.assert_not_called()
            self.panel.submit()
            self.panel.set_sleep_paused(True)
            self.panel.set_sleep_paused(False)
            self.panel._confirmation.button(QMessageBox.StandardButton.Yes).click()
            generate.assert_not_called()
        self.assertEqual(self.panel.input.toPlainText(), "QA_INPUT")

    def test_comparison_retains_first_success_when_second_fails_with_same_frozen_input(self):
        self._submit_offline("QA_ORIGINAL")
        self.panel.result.setPlainText("QA_EDITED_PREVIOUS")
        self.panel.input.setPlainText("QA_REVISION")
        calls, retained = [], []
        release = threading.Event()

        def generate(original, **kwargs):
            calls.append((original, kwargs))
            if len(calls) == 1:
                return {"result": "QA_FIRST", "notes": []}
            if not release.wait(3):
                raise TimeoutError("first result was not delivered")
            raise ValueError("QA_SECOND_FAILED")

        def first_ready():
            if self.panel.result.toPlainText() == "QA_FIRST":
                retained.append((self.panel.is_busy, list(self.panel.revisions)))
                release.set()
        self.panel.result_ready.connect(first_ready)
        with patch("optimizer_chat.optimize_ai", side_effect=generate):
            self.panel.compare.click()
            first, second = self.panel._comparison_choices
            first.setCurrentIndex(first.findData("vireonix"))
            second.setCurrentIndex(second.findData("pollinations-classic"))
            button = next(b for b in self.panel._comparison_dialog.findChildren(QPushButton)
                          if b.accessibleName() == "비교 요청 확인")
            button.click()
            self.panel._confirmation.button(QMessageBox.StandardButton.Yes).click()
            self._wait_idle()
        self.assertEqual(retained, [(True, ["QA_REVISION"])])
        self.assertEqual(self.panel.result.toPlainText(), "QA_FIRST")
        self.assertEqual(self.panel.revisions, ["QA_REVISION"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], calls[1][0])
        for key in ("context", "revision", "previous_result", "task", "detail"):
            self.assertEqual(calls[0][1][key], calls[1][1][key])
        self.assertEqual(calls[1][1]["previous_result"], "QA_EDITED_PREVIOUS")
        self.assertEqual(self.panel.result_choice.currentData()["provider"], "vireonix")
        self.assertNotIn("QA_SECOND_FAILED", self.panel.status.text())

    def test_quality_view_recomputes_current_edits_and_schema_one_still_restores(self):
        self._submit_offline("30명 이내")
        self.panel.diff.click()
        self.panel.result.setPlainText("QA_CHANGED")
        with patch("prompt_quality.condition_report", return_value=[]) as report:
            self.panel.result.setPlainText("QA_CURRENT")
            self.assertEqual(report.call_args.args[2], "QA_CURRENT")
        self.panel._diff_dialog.close()
        state = self.panel.export_update_state()
        metadata = self.panel.result_choice.currentData()
        self.panel._clear_request()
        self.panel.restore_update_state(state)
        self.assertEqual(self.panel.result_choice.currentData(), metadata)
        self.assertEqual(self.panel.result.toPlainText(), "QA_CURRENT")
        self.panel._clear_request()
        state.pop("clarifications")
        state.pop("result_metadata")
        self.panel.restore_update_state(state)
        self.assertEqual(self.panel.result.toPlainText(), "QA_CURRENT")
        self.assertEqual(self.panel._clarifications, "")
        self.assertIsNone(self.panel.result_choice.currentData())

    def test_jarvisclaw_consent_privacy_and_explicit_generation(self):
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("jarvisclaw"))
        self.panel.input.setPlainText("QA_SYNTHETIC")
        with patch("optimizer_chat.optimize_ai", return_value={"result": "QA_RESULT", "notes": []}) as ai:
            self.panel.submit()
            self.assertIn("JarvisClaw", self.panel._confirmation.text())
            self.assertIn("외부 모델 제공자", self.panel._confirmation.text())
            self.assertIn("메타데이터", self.panel._confirmation.text())
            self.assertNotIn("90일", self.panel._confirmation.text())
            self.panel._confirmation.button(QMessageBox.StandardButton.No).click()
            self.app.processEvents()
            ai.assert_not_called()
            self.assertEqual(self.panel.input.toPlainText(), "QA_SYNTHETIC")
            self.panel.submit()
            self.panel._confirmation.button(QMessageBox.StandardButton.Yes).click()
            self._wait_idle()
            self.assertEqual(ai.call_args.kwargs["provider"], "jarvisclaw")
        self.assertEqual(self.panel.result.toPlainText(), "QA_RESULT")
        self.assertEqual(self.panel.result_choice.currentData()["model"], "auto/free")

    def test_jarvisclaw_comparison_requires_provider_specific_consent(self):
        self.panel.input.setPlainText("QA_SYNTHETIC")
        with patch("optimizer_chat.optimize_ai") as ai:
            self.panel.compare.click()
            first, second = self.panel._comparison_choices
            first.setCurrentIndex(first.findData("jarvisclaw"))
            second.setCurrentIndex(second.findData("offline"))
            button = next(b for b in self.panel._comparison_dialog.findChildren(QPushButton)
                          if b.accessibleName() == "비교 요청 확인")
            button.click()
            self.assertIn("JarvisClaw", self.panel._confirmation.text())
            self.assertIn("외부 모델 제공자", self.panel._confirmation.text())
            self.panel._confirmation.button(QMessageBox.StandardButton.No).click()
            self.app.processEvents()
            ai.assert_not_called()
        self.assertEqual(self.panel.input.toPlainText(), "QA_SYNTHETIC")

    def test_jarvisclaw_failure_offers_manual_fallback_with_new_consent(self):
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("jarvisclaw"))
        self.panel.input.setPlainText("QA_INPUT")
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", side_effect=RuntimeError("QA_PRIVATE")) as ai:
            self.panel.submit()
            self._wait_idle()
            self.assertEqual(ai.call_count, 1)
        self.assertTrue(self.panel.retry_other.isVisible())
        with patch("optimizer_chat.optimize_ai") as ai:
            self.panel.retry_other.click()
            self.assertEqual(self.panel.provider.currentData(), "pollinations-classic")
            self.assertTrue(self.panel._confirmation.isVisible())
            ai.assert_not_called()
            self.panel._confirmation.button(QMessageBox.StandardButton.No).click()
            self.app.processEvents()
            ai.assert_not_called()
        self.assertEqual(self.panel.input.toPlainText(), "QA_INPUT")

    def test_quality_view_localizes_all_condition_types_and_changes(self):
        self.panel.input.setPlainText("월요일 10시 30명")
        self.panel.result.setPlainText("화요일 11시 20명")
        kinds = ("number", "date", "time", "weekday", "quote", "prohibition")
        findings = [{"kind": kind, "literal": "QA", "status": "missing",
                     "source": "original"} for kind in kinds]
        findings.append({"kind": "time", "literal": "QA_CONTEXT", "status": "present",
                         "source": "context"})
        with patch("prompt_quality.condition_report", return_value=findings):
            text = self.panel._quality_text()
        for label in ("수량·숫자", "날짜", "시간", "요일", "인용 문구", "금지 조건",
                      "문자 일치 없음", "문자 일치", "원문", "추가 조건", "변경"):
            self.assertIn(label, text)
        for raw in ("number:", "date:", "time:", "weekday:", "quote:",
                    "prohibition:", "missing", "present", "original", "context", "replace"):
            self.assertNotIn(raw, text)

    def test_comparison_success_keeps_two_aligned_results_and_one_revision(self):
        self._submit_offline("QA_ORIGINAL")
        self.panel.input.setPlainText("QA_REVISION")
        ready = []
        self.panel.result_ready.connect(lambda: ready.append(self.panel.result.toPlainText()))
        expected = self.panel._request_snapshot()
        frozen = self.panel._generation_input("QA_REVISION")
        with patch("optimizer_chat.optimize_ai", side_effect=[
                {"result": "QA_A", "notes": []}, {"result": "QA_B", "notes": []}]):
            self.panel._start_comparison(expected, frozen,
                                          ["vireonix", "pollinations-classic"], [None, None])
            self._wait_idle()
        self.assertEqual(ready, ["QA_A", "QA_B"])
        self.assertEqual(self.panel.revisions, ["QA_REVISION"])
        self.assertEqual(self.panel.conversation.toPlainText().count("QA_REVISION"), 1)
        self.assertEqual(self.panel.result_choice.itemData(1)["provider"], "vireonix")
        self.assertEqual(self.panel.result_choice.itemData(2)["provider"], "pollinations-classic")
        self.panel.result_choice.setCurrentIndex(1)
        self.assertEqual(self.panel.result.toPlainText(), "QA_A")

    def test_default_jarvisclaw_never_sends_without_confirmation(self):
        self.assertEqual(self.panel.provider.currentData(), "jarvisclaw")
        self.assertIn("전송 전 다시 확인", self.panel.provider_note.text())
        with patch("optimizer_chat.optimize_ai") as ai, \
             patch("optimizer_chat.threading.Thread") as thread:
            fresh = PromptConversation()
            self.assertEqual(fresh.provider.currentData(), "jarvisclaw")
            self.app.processEvents()
            ai.assert_not_called()
            thread.assert_not_called()
            fresh.close()
            self.panel.input.setPlainText("가상의 업무 요청")
            self.panel.submit()
            self.assertTrue(self.panel._confirmation.isVisible())
            self.assertIn("JarvisClaw", self.panel._confirmation.text())
            ai.assert_not_called()
            thread.assert_not_called()
            self.panel._confirmation.button(QMessageBox.StandardButton.No).click()
            self.app.processEvents()
            ai.assert_not_called()
            thread.assert_not_called()
            self.assertEqual(self.panel.input.toPlainText(), "가상의 업무 요청")

    def test_placeholder_example_does_not_become_a_request(self):
        example = self.panel.input.placeholderText()
        self.assertIn("연수 안내문", example)
        self.assertIn("[확인 필요]", example)
        self.assertNotIn("\n", example)  # QTextEdit only paints one placeholder line.
        self.assertEqual(self.panel.input.accessibleDescription(), example)
        placeholder_color = self.panel.input.palette().color(QPalette.ColorRole.PlaceholderText)
        self.assertEqual((placeholder_color.name(), placeholder_color.alpha()), ("#586d7a", 255))
        self.assertEqual(self.panel.input.toPlainText(), "")
        self.assertFalse(self.panel.has_pending_work())
        with patch("optimizer_chat.optimize_ai") as ai:
            self.panel.submit()
            self.app.processEvents()
            self.assertIn("입력해 주세요", self.panel.inline_notice.text())
            self.assertFalse(hasattr(self.panel, "_confirmation"))
            ai.assert_not_called()

    def test_example_works_offline_and_tracks_conversation_state(self):
        initial = self.panel.input.placeholderText()
        example = initial.removeprefix("예: ").replace("\n", " ")
        self._submit_offline(example)
        self.assertEqual(self.panel.original, example)
        self.assertIn(example, self.panel.result.toPlainText())
        self.assertIn("[확인 필요]", self.panel.result.toPlainText())
        self.assertIn("세 항목", self.panel.input.placeholderText())
        self.panel.input.setPlainText("사용자가 작성 중인 추가 조건")
        self.assertEqual(self.panel.input.toPlainText(), "사용자가 작성 중인 추가 조건")
        self.panel._clear_request()
        self.assertEqual(self.panel.input.placeholderText(), initial)
        self.panel._apply_library({"prompt": example, "result": "저장된 결과"})
        self.assertIn("세 항목", self.panel.input.placeholderText())
        self.assertEqual(self.panel.result.toPlainText(), "저장된 결과")

    def test_compact_history_toggle_preserves_the_conversation_and_draft(self):
        self._submit_offline("회의 메모를 정리해 주세요")
        self.panel.input.setPlainText("추가할 조건")
        history = self.panel.conversation.toPlainText()
        self.assertFalse(self.panel.conversation.isVisible())
        self.panel.history_toggle.setFocus()
        self._key(self.panel.history_toggle, Qt.Key.Key_Space)
        self.assertTrue(self.panel.conversation.isVisible())
        self.assertEqual(self.panel.conversation.toPlainText(), history)
        self.assertEqual(self.panel.input.toPlainText(), "추가할 조건")
        self.panel.history_toggle.click()
        self.assertFalse(self.panel.conversation.isVisible())
        self.assertEqual(self.panel.conversation.toPlainText(), history)

    def test_busy_signal_emits_only_transitions_and_finishes_on_failure(self):
        transitions = []
        self.panel.busy_changed.connect(transitions.append)
        self.panel._set_busy(False)
        self.panel._set_busy(True)
        self.panel._set_busy(True)
        self.panel._pending = ("원문", "원문", "", "vireonix")
        self.panel._fail("연결 실패")
        self.assertEqual(transitions, [True, False])
        self.assertFalse(self.panel.is_busy)

    def test_external_wait_shows_provider_and_keeps_draft_until_completion(self):
        started, release = threading.Event(), threading.Event()
        def delayed(*_args, **_kwargs):
            started.set()
            if not release.wait(3):
                raise RuntimeError("test worker not released")
            return {"result": "QA_RESULT", "notes": []}
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("pollinations-classic"))
        self.panel.input.setPlainText("QA_INPUT")
        self.panel.result.setPlainText("QA_DRAFT")
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", side_effect=delayed):
            try:
                self.panel.submit()
                self.assertTrue(started.wait(2))
                self.assertIn("Pollinations Classic", self.panel.status.text())
                self.assertIn("1분 이상", self.panel.status.text())
                self.assertEqual(self.panel.input.toPlainText(), "QA_INPUT")
                self.assertEqual(self.panel.result.toPlainText(), "QA_DRAFT")
                self.assertTrue(self.panel.is_busy)
            finally:
                release.set()
            self._wait_idle()
        self.assertEqual(self.panel.result.toPlainText(), "QA_RESULT")

    def test_worker_errors_do_not_expose_private_exception_text(self):
        from optimizer_ai import OptimizationError
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("QA_INPUT")
        self.panel.result.setPlainText("QA_DRAFT")
        for error in (RuntimeError("QA_SECRET QA_PRIVATE"),
                      OptimizationError("QA_SECRET QA_PRIVATE")):
            with self.subTest(error=type(error).__name__), \
                    patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                    patch("optimizer_chat.optimize_ai", side_effect=error):
                self.panel.submit()
                self._wait_idle()
                self.assertNotIn("QA_SECRET", self.panel.status.text())
                self.assertNotIn("QA_PRIVATE", self.panel.status.text())
                self.assertEqual(self.panel.input.toPlainText(), "QA_INPUT")
                self.assertEqual(self.panel.result.toPlainText(), "QA_DRAFT")
        from optimizer_chat import _safe_generation_error
        message = "AI 서비스가 일시적으로 응답하지 않습니다 (HTTP 502). 잠시 후 다시 시도해 주세요."
        self.assertEqual(_safe_generation_error(OptimizationError(message)), message)
        self.assertNotIn("QA_SECRET", _safe_generation_error(OptimizationError(message + " QA_SECRET")))
        payment = "JarvisClaw 무료 요청을 처리할 수 없습니다 (HTTP 402). 잠시 후 다시 시도해 주세요."
        self.assertEqual(_safe_generation_error(OptimizationError(payment)), payment)
        self.assertNotIn("QA_SECRET", _safe_generation_error(OptimizationError(payment + " QA_SECRET")))

    def test_thread_start_failure_resets_busy_and_preserves_input(self):
        transitions = []
        self.panel.busy_changed.connect(transitions.append)
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("offline"))
        self.panel.input.setPlainText("보존할 요청")
        with patch("optimizer_chat.threading.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("thread creation failed")
            self.panel.submit()
        self.assertEqual(transitions, [True, False])
        self.assertTrue(self.panel.send.isEnabled())
        self.assertEqual(self.panel.input.toPlainText(), "보존할 요청")
        self.assertIn("시작하지 못했습니다", self.panel.status.text())

    def test_first_request_and_refinement_preserve_original_and_edited_result(self):
        self._submit_offline("회의록을 정리해 주세요. 3개 항목을 유지하세요.")
        first = self.panel.result.toPlainText()
        self.assertIn("회의록을 정리해 주세요. 3개 항목을 유지하세요.", first)
        self.assertIn("기본", self.panel.conversation.toPlainText())
        self.assertIn("AI", self.panel.status.text())
        self.panel.result.setPlainText(first + "\n사용자가 직접 추가한 조건")
        self._submit_offline("표 형식으로 작성해 주세요.")
        second = self.panel.result.toPlainText()
        self.assertEqual(self.panel.original, "회의록을 정리해 주세요. 3개 항목을 유지하세요.")
        self.assertEqual(self.panel.revisions, ["표 형식으로 작성해 주세요."])
        self.assertIn(self.panel.original, second)
        self.assertIn("표 형식으로 작성해 주세요.", second)
        self.assertIn("사용자가 직접 추가한 조건", second)
        self.assertEqual(self.panel.result_choice.count(), 2)
        self.panel.result_choice.setCurrentIndex(0)
        self.assertTrue(self.panel.result.toPlainText().endswith("사용자가 직접 추가한 조건"))
        self.panel.result_choice.setCurrentIndex(1)
        self.assertEqual(self.panel.result.toPlainText(), second)

    def test_first_result_keeps_an_unslotted_user_draft_as_selectable_history(self):
        draft = "결과를 만들기 전에 직접 쓴 초안\n두 번째 줄"
        self.panel.result.setPlainText(draft)
        completed = []
        self.panel.result_ready.connect(lambda: completed.append(True))
        self._submit_offline("회의 내용을 요약해 주세요")
        self.assertEqual(completed, [True])
        generated = self.panel.result.toPlainText()
        self.assertIn("회의 내용을 요약해 주세요", generated)
        self.assertEqual(self.panel.result_choice.count(), 2)
        self.assertEqual(self.panel.result_choice.itemText(0), "직접 작성한 결과")
        self.assertIn("미리 작성한 결과", self.panel.status.text())
        self.panel.result_choice.setCurrentIndex(0)
        self.assertEqual(self.panel.result.toPlainText(), draft)
        self.panel.result_choice.setCurrentIndex(1)
        self.assertEqual(self.panel.result.toPlainText(), generated)

    def test_first_ai_request_never_transmits_unslotted_draft_and_keeps_it(self):
        draft = "외부에 보내지 않을 결과 초안"
        self.panel.result.setPlainText(draft)
        self.panel.input.setPlainText("합성 원문")
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", return_value={"result": "새 결과", "notes": []}) as ai:
            self.panel.submit()
            self._wait_idle()
            self.assertEqual(ai.call_args.kwargs["previous_result"], "")
        self.assertEqual(self.panel.result.toPlainText(), "새 결과")
        self.panel.result_choice.setCurrentIndex(0)
        self.assertEqual(self.panel.result.toPlainText(), draft)

    def test_first_failure_and_changes_during_processing_preserve_result_draft(self):
        self.panel.result.setPlainText("수정 전")
        self.panel.input.setPlainText("합성 원문")
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", side_effect=RuntimeError("연결 실패")):
            self.panel.submit()
            self._wait_idle()
        self.assertEqual(self.panel.result.toPlainText(), "수정 전")
        self.assertEqual(self.panel.result_choice.count(), 0)

        started, release = threading.Event(), threading.Event()
        def delayed(*_args, **_kwargs):
            started.set()
            if not release.wait(3):
                raise TimeoutError("test worker not released")
            return {"result": "새 결과", "notes": []}
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", side_effect=delayed):
            try:
                self.panel.submit()
                self.assertTrue(started.wait(2))
                self.panel.result.setPlainText("처리 중 바뀐 초안")
            finally:
                release.set()
            self._wait_idle()
        self.assertEqual(self.panel.result.toPlainText(), "새 결과")
        self.panel.result_choice.setCurrentIndex(0)
        self.assertEqual(self.panel.result.toPlainText(), "처리 중 바뀐 초안")

    def test_cancelling_external_confirmation_keeps_input_and_never_calls_provider(self):
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("전송 전 유지할 원문")
        with patch.object(self.panel, "_confirm") as confirm, patch("optimizer_chat.optimize_ai") as ai:
            self.panel.submit()
            self.assertEqual(confirm.call_count, 1)
            ai.assert_not_called()
            self.assertFalse(self.panel._busy)
            self.assertEqual(self.panel.input.toPlainText(), "전송 전 유지할 원문")
            self.assertEqual(self.panel.original, "")

    def test_ai_quantity_change_is_visible_in_conversation_review(self):
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("광합성을 세 문장으로 설명해 주세요.")
        reply = {"result": "광합성을 세 문장 이내로 설명해 주세요.", "notes": [], "mode": "ai"}
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", return_value=reply):
            self.panel.submit()
            self._wait_idle()
        self.assertIn("수량 범위 확인 필요", self.panel.status.text())
        self.assertEqual(self.panel.result.toPlainText(), reply["result"])

    def test_clarification_review_uses_frozen_context_when_changed_during_generation(self):
        self.panel.input.setPlainText("연수 안내문을 작성해 주세요")
        self.panel._clarifications = "deadline: 11월 5일\n학부모를 제외"
        started, release = threading.Event(), threading.Event()
        def generate(_original, **kwargs):
            self.assertIn("11월 5일", kwargs["context"])
            self.assertIn("학부모를 제외", kwargs["context"])
            started.set()
            if not release.wait(3):
                raise TimeoutError("test worker not released")
            return {"result": "연수 안내문을 작성해 주세요", "notes": []}
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", side_effect=generate):
            try:
                self.panel.submit()
                self.assertTrue(started.wait(2))
                # A previously opened modeless condition dialog can apply later.
                self.panel._clarifications = "deadline: 12월 8일"
            finally:
                release.set()
            self._wait_idle()
        status = self.panel.status.text()
        self.assertIn("11월 5일", status)
        self.assertIn("학부모를 제외", status)
        self.assertNotIn("12월 8일", status)
        self.assertEqual(self.panel.result.toPlainText(), "연수 안내문을 작성해 주세요")

    def test_comparison_reselection_reviews_each_frozen_result(self):
        self.panel.input.setPlainText("연수 안내문을 작성해 주세요")
        self.panel._clarifications = "deadline: 11월 5일\n학부모를 제외"
        expected = self.panel._request_snapshot()
        frozen = self.panel._generation_input(self.panel.input.toPlainText())
        with patch("optimizer_chat.optimize_ai", side_effect=[
                {"result": "연수 안내문을 준비해 주세요", "notes": []},
                {"result": "11월 5일 연수 안내문을 준비하고 학부모를 제외", "notes": []}]):
            self.panel._start_comparison(expected, frozen,
                                          ["jarvisclaw", "vireonix"], [None, None])
            self._wait_idle()
        self.assertNotIn("날짜·요일·시간 확인 필요", self.panel.status.text())
        self.panel._clarifications = "deadline: 12월 8일"
        self.panel.result_choice.setCurrentIndex(0)
        self.assertIn("11월 5일", self.panel.status.text())
        self.assertIn("학부모를 제외", self.panel.status.text())
        self.assertNotIn("12월 8일", self.panel.status.text())
        self.panel.result_choice.setCurrentIndex(1)
        self.assertNotIn("날짜·요일·시간 확인 필요", self.panel.status.text())
        self.assertNotIn("금지 조건 원문 확인 필요", self.panel.status.text())

    def test_comparison_second_missing_condition_does_not_mark_first(self):
        self.panel.input.setPlainText("연수 안내문을 작성해 주세요")
        self.panel._clarifications = "deadline: 11월 5일"
        expected = self.panel._request_snapshot()
        frozen = self.panel._generation_input(self.panel.input.toPlainText())
        with patch("optimizer_chat.optimize_ai", side_effect=[
                {"result": "11월 5일 연수 안내문을 준비해 주세요", "notes": []},
                {"result": "연수 안내문을 준비해 주세요", "notes": []}]):
            self.panel._start_comparison(expected, frozen,
                                          ["jarvisclaw", "vireonix"], [None, None])
            self._wait_idle()
        self.assertIn("11월 5일", self.panel.status.text())
        self.panel.result_choice.setCurrentIndex(0)
        self.assertNotIn("날짜·요일·시간 확인 필요", self.panel.status.text())
        self.panel.result_choice.setCurrentIndex(1)
        self.assertIn("11월 5일", self.panel.status.text())

    def test_comparison_wait_and_failure_keep_first_result_warning(self):
        self.panel.input.setPlainText("연수 안내문을 작성해 주세요")
        self.panel._clarifications = "deadline: 11월 5일"
        expected = self.panel._request_snapshot()
        frozen = self.panel._generation_input(self.panel.input.toPlainText())
        release, waiting = threading.Event(), []
        def generate(*_args, **_kwargs):
            if not waiting:
                waiting.append("started")
                return {"result": "연수 안내문을 준비해 주세요", "notes": []}
            if not release.wait(3):
                raise TimeoutError("test worker not released")
            raise ValueError("fixture")
        def first_ready():
            # _finish emits before the comparison handler adds its wait notice.
            QTimer.singleShot(0, capture_wait)
        def capture_wait():
            waiting.append(self.panel.status.text())
            release.set()
        self.panel.result_ready.connect(first_ready)
        with patch("optimizer_chat.optimize_ai", side_effect=generate):
            self.panel._start_comparison(expected, frozen,
                                          ["jarvisclaw", "vireonix"], [None, None])
            self._wait_idle()
        self.assertIn("두 번째 모델", waiting[1])
        self.assertIn("11월 5일", waiting[1])
        self.assertIn("11월 5일", self.panel.status.text())
        self.assertEqual(self.panel.result.toPlainText(), "연수 안내문을 준비해 주세요")

    def test_refining_an_older_edited_result_preserves_later_conditions(self):
        self._submit_offline("원래 요청")
        self.panel.result.setPlainText(self.panel.result.toPlainText() + "\n직접 수정한 결과")
        self._submit_offline("첫 추가 조건")
        self._submit_offline("두 번째 추가 조건")
        self.panel.result_choice.setCurrentIndex(0)
        self.assertIn("직접 수정한 결과", self.panel.result.toPlainText())
        self._submit_offline("세 번째 추가 조건")
        result = self.panel.result.toPlainText()
        for content in ("직접 수정한 결과", "첫 추가 조건", "두 번째 추가 조건", "세 번째 추가 조건"):
            self.assertEqual(result.count(content), 1, content)
        self.assertEqual(self.panel.revisions, ["첫 추가 조건", "두 번째 추가 조건", "세 번째 추가 조건"])

    def test_failed_external_request_preserves_draft_and_previous_result(self):
        self._submit_offline("원래 요청을 정리해 주세요")
        self.panel.result.setPlainText("직접 수정한 기존 결과")
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("추가 조건도 유지")
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", side_effect=RuntimeError("연결 실패")) as ai:
            self.panel.submit()
            self._wait_idle()
            ai.assert_called_once()
        self.assertEqual(self.panel.input.toPlainText(), "추가 조건도 유지")
        self.assertEqual(self.panel.original, "원래 요청을 정리해 주세요")
        self.assertEqual(self.panel.result.toPlainText(), "직접 수정한 기존 결과")
        self.assertEqual(self.panel.revisions, [])
        self.assertIn("연결 실패", self.panel.status.text())
        self.assertEqual(self.panel.provider.currentData(), "vireonix")
        self.assertIn("션에게 보내기", self.panel.status.text())
        self.assertIn("Pollinations Classic으로 보내기", self.panel.status.text())
        self.assertIn("기본 정리는 AI 재작성이 아닙니다", self.panel.status.text())

    def test_switching_service_after_error_requires_new_consent(self):
        self._submit_offline("원래 요청")
        self.panel.result.setPlainText("이전 결과")
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("추가 조건")
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", side_effect=RuntimeError("서비스 연결 실패")) as ai:
            self.panel.submit()
            self._wait_idle()
            self.assertEqual(ai.call_count, 1)
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("pollinations-classic"))
        with patch.object(self.panel, "_confirm") as confirm, patch("optimizer_chat.optimize_ai") as ai:
            self.panel.submit()
            self.assertEqual(confirm.call_count, 1)
            self.assertIn("Pollinations Classic", confirm.call_args.args[1])
            ai.assert_not_called()
        self.assertEqual(self.panel.input.toPlainText(), "추가 조건")
        self.assertEqual(self.panel.result.toPlainText(), "이전 결과")
        self.assertEqual(self.panel.provider.currentData(), "pollinations-classic")

    def test_failed_ai_offers_other_service_and_still_asks_consent(self):
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.assertFalse(self.panel.retry_other.isVisible())
        self.panel.input.setPlainText("합성 원문")
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", side_effect=RuntimeError("HTTP 502")):
            self.panel.submit()
            self._wait_idle()
        self.assertTrue(self.panel.retry_other.isVisible())
        self.assertEqual(self.panel.retry_other.text(), "Pollinations Classic으로 보내기")
        with patch.object(self.panel, "_confirm") as confirm, patch("optimizer_chat.optimize_ai") as ai:
            self.panel.retry_other.click()
            self.assertEqual(confirm.call_count, 1)
            self.assertIn("Pollinations Classic", confirm.call_args.args[1])
            ai.assert_not_called()
        self.assertEqual(self.panel.provider.currentData(), "pollinations-classic")
        self.assertFalse(self.panel.retry_other.isVisible())
        self.assertEqual(self.panel.input.toPlainText(), "합성 원문")
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", return_value={"result": "결과", "notes": []}) as ai:
            self.panel.submit()
            self._wait_idle()
            self.assertEqual(ai.call_args.kwargs["provider"], "pollinations-classic")
        self.assertFalse(self.panel.retry_other.isVisible())
        self.assertIn("예비", self.panel.provider.currentText())
        self.assertIn("30초", self.panel.provider_note.text())

    def test_offline_failure_and_sleep_do_not_offer_a_usable_other_service(self):
        self.panel._pending = ("원문", "원문", "", "offline")
        self.panel._fail("입력 오류")
        self.assertFalse(self.panel.retry_other.isVisible())
        self.panel._pending = ("원문", "원문", "", "pollinations-classic")
        self.panel.set_sleep_paused(True)
        self.panel._fail("서비스 오류")
        self.assertEqual(self.panel.retry_other.text(), "Vireonix로 보내기")
        self.assertFalse(self.panel.retry_other.isEnabled())
        with patch.object(self.panel, "_confirm") as confirm:
            self.panel._send_to_other_ai()
            confirm.assert_not_called()
        self.panel.set_sleep_paused(False)
        self.assertTrue(self.panel.retry_other.isEnabled())

    def test_fill_panel_fills_placeholders_in_one_undoable_step(self):
        original = "[학교명] 학부모님께 [대상 학년] 안내문을 써 주세요. 서명: [학교명]\n자료:\n[자료 붙여 넣기]"
        self.panel.result.setPlainText(original)
        self.assertTrue(self.panel.fill_toggle.isVisible())
        self.assertEqual(self.panel.fill_toggle.text(), "빈칸 채우기 (3)")
        self.assertFalse(self.panel.fill_panel.isVisible())
        self.panel.fill_toggle.click()
        self.assertTrue(self.panel.fill_panel.isVisible())
        self.assertEqual(list(self.panel._fill_fields), ["학교명", "대상 학년"])
        self.assertIn("직접 붙여", self.panel.fill_hint.text())
        self.panel._fill_fields["학교명"].setText("가상초등학교")
        self.panel.fill_apply.click()
        filled = self.panel.result.toPlainText()
        self.assertEqual(filled.count("가상초등학교"), 2)
        self.assertIn("[대상 학년]", filled)
        self.assertEqual(list(self.panel._fill_fields), ["대상 학년"])
        self.assertIn("빈칸 1개", self.panel.status.text())
        self.panel.result.undo()
        self.assertEqual(self.panel.result.toPlainText(), original)

    def test_fill_panel_hides_without_placeholders_and_keeps_typed_values(self):
        self.panel.result.setPlainText("빈칸이 없는 결과")
        self.assertFalse(self.panel.fill_toggle.isVisible())
        self.panel.result.setPlainText("[학교명] 안내")
        self.panel.fill_toggle.click()
        self.panel._fill_fields["학교명"].setText("가상초등학교")
        self.panel.result.setPlainText("[학교명] 안내, 문의 [담당자 연락처]")
        self.assertEqual(self.panel._fill_fields["학교명"].text(), "가상초등학교")
        self.panel.fill_apply.click()
        self.assertEqual(self.panel.result.toPlainText(), "가상초등학교 안내, 문의 [담당자 연락처]")
        self.panel.copy.click()
        self.assertIn("채우지 않은 [대괄호] 칸이 1개", self.panel.status.text())
        self.panel.result.setPlainText("완성된 결과")
        self.assertFalse(self.panel.fill_toggle.isVisible())
        self.assertFalse(self.panel.fill_panel.isVisible())

    def test_failure_while_asleep_keeps_send_disabled(self):
        self.panel._pending = ("원문", "원문", "", "vireonix")
        self.panel.input.setPlainText("원문")
        self.panel._set_busy(True)
        self.panel.set_sleep_paused(True)
        self.panel._fail("서비스 오류")
        self.assertFalse(self.panel.send.isEnabled())
        self.assertEqual(self.panel.input.toPlainText(), "원문")
        self.assertIn("션에게 보내기", self.panel.status.text())
        self.assertIn("션을 깨운 뒤", self.panel.status.text())

    def test_ai_receives_explicitly_confirmed_previous_edit_and_revision(self):
        self._submit_offline("원래 요청")
        self.panel.result.setPlainText("내가 수정한 결과")
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("pollinations-classic"))
        self.panel.input.setPlainText("간단한 표로")
        reply = {"result": "새 결과", "notes": []}
        with patch.object(self.panel, "_confirm", side_effect=lambda _title, _body, callback: callback()), \
                patch("optimizer_chat.optimize_ai", return_value=reply) as ai:
            self.panel.submit()
            self._wait_idle()
            self.assertEqual(ai.call_args.kwargs["previous_result"], "내가 수정한 결과")
            self.assertEqual(ai.call_args.kwargs["revision"], "간단한 표로")
            self.assertEqual(ai.call_args.kwargs["provider"], "pollinations-classic")
        self.assertEqual(self.panel.result.toPlainText(), "새 결과")
        self.assertEqual(self.panel.input.toPlainText(), "")

    def test_template_inserts_into_current_input_without_discarding_it(self):
        self.panel.input.setPlainText("기존 요청 ")
        cursor = self.panel.input.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.panel.input.setTextCursor(cursor)
        with patch("optimizer_templates.show_template_picker") as picker:
            self.panel.template.click()
            callback = picker.call_args.args[1]
            callback("템플릿 본문")
        self.assertEqual(self.panel.input.toPlainText(), "기존 요청 템플릿 본문")

    def test_modeless_template_selection_during_request_preserves_changed_input(self):
        from optimizer_core import create_offline_prompt

        self.panel.provider.setCurrentIndex(self.panel.provider.findData("offline"))
        release = threading.Event()
        started = threading.Event()

        def delayed(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("test worker timed out")
            return create_offline_prompt(*args, **kwargs)

        self.panel.template.click()
        picker = self.panel._template_dialog
        picker.list.setCurrentRow(0)
        picker.preview.setPlainText("사용자가 고른 템플릿")
        self.panel.input.setPlainText("원래 요청")
        with patch("optimizer_chat.create_offline_prompt", side_effect=delayed):
            try:
                self.panel.submit()
                self.assertTrue(started.wait(2))
                self.assertTrue(self.panel._busy)
                picker.select_button.click()
                changed = self.panel.input.toPlainText()
                self.assertIn("사용자가 고른 템플릿", changed)
            finally:
                release.set()
            self._wait_idle()
        self.assertEqual(self.panel.original, "원래 요청")
        self.assertEqual(self.panel.input.toPlainText(), changed)
        self.assertIn("그대로 남겨", self.panel.status.text())

    def test_changed_input_after_external_confirmation_is_not_transmitted(self):
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("확인한 원문")
        with patch.object(self.panel, "_confirm") as confirm, patch("optimizer_chat.optimize_ai") as ai:
            self.panel.submit()
            callback = confirm.call_args.args[2]
            self.panel.input.setPlainText("확인 후 달라진 원문")
            callback()
            self.app.processEvents()
            ai.assert_not_called()
        self.assertEqual(self.panel.input.toPlainText(), "확인 후 달라진 원문")
        self.assertFalse(self.panel._busy)
        self.assertIn("전송하지 않았어요", self.panel.status.text())

    def test_new_request_confirmation_does_not_clear_later_edits(self):
        self.panel.input.setPlainText("처음 입력")
        with patch.object(self.panel, "_confirm") as confirm:
            self.panel.reset.click()
            callback = confirm.call_args.args[2]
            self.panel.input.setPlainText("확인 중 추가한 내용")
            callback()
        self.assertEqual(self.panel.input.toPlainText(), "확인 중 추가한 내용")
        self.assertIn("시작하지 않았어요", self.panel.status.text())

    def test_library_restore_asks_again_if_draft_changes_while_open(self):
        self.panel.input.setPlainText("기존 입력")
        with tempfile.TemporaryDirectory(prefix="syeon-restore-race-") as temp:
            store = LibraryStore(Path(temp) / "library.json")
            with patch("optimizer_library.LibraryStore", return_value=store):
                self.panel._open_library()
                self.panel.input.setPlainText("선택창에서 추가한 입력")
                with patch.object(self.panel, "_confirm") as confirm:
                    self.panel._restore_library({"prompt": "보관 원문", "result": "보관 결과"})
                    self.assertEqual(confirm.call_count, 1)
                    callback = confirm.call_args.args[2]
                    self.panel.input.setPlainText("다시 바뀐 입력")
                    callback()
                self.assertEqual(self.panel.input.toPlainText(), "다시 바뀐 입력")
                self.assertEqual(self.panel.original, "")
                self.panel._library_dialog.close()

    def test_new_request_requires_confirmation_and_cancel_keeps_everything(self):
        self._submit_offline("원래 요청")
        self.panel.result.setPlainText("수정 중인 결과")
        self.panel.input.setPlainText("작성 중인 추가 조건")
        with patch.object(self.panel, "_confirm") as confirm:
            self.panel.reset.click()
            self.assertEqual(confirm.call_count, 1)
        self.assertEqual(self.panel.original, "원래 요청")
        self.assertEqual(self.panel.input.toPlainText(), "작성 중인 추가 조건")
        self.assertEqual(self.panel.result.toPlainText(), "수정 중인 결과")

    def test_older_result_backup_import_restore_and_refinement_keep_revisions(self):
        self._submit_offline("원래 요청")
        self._submit_offline("첫 번째 추가 조건")
        self._submit_offline("두 번째 추가 조건")
        self.panel.result_choice.setCurrentIndex(0)
        self.panel.result.setPlainText(self.panel.result.toPlainText() + "\n이전 결과 직접 편집")
        visible_result = self.panel.result.toPlainText()
        with tempfile.TemporaryDirectory(prefix="syeon-library-test-") as temp:
            root = Path(temp)
            backup = root / "backup.json"
            first_store = LibraryStore(root / "first.json")
            second_store = LibraryStore(root / "second.json")
            with patch("optimizer_library.LibraryStore", return_value=first_store), \
                    patch.object(LibraryDialog, "_notify"):
                self.panel._open_library()
                original_dialog = self.panel._library_dialog
                self.assertEqual(self.panel.result.toPlainText(), visible_result)
                original_dialog._save()
                with patch.object(original_dialog, "_file_dialog", side_effect=lambda _title, _save, handler: handler(str(backup))):
                    original_dialog._export()
                self.assertTrue(backup.exists())
                self.assertEqual(first_store.load()[0]["syeonRevisions"], ["첫 번째 추가 조건", "두 번째 추가 조건"])
                original_dialog.close()
            with patch("optimizer_library.LibraryStore", return_value=second_store), \
                    patch.object(LibraryDialog, "_notify"):
                self.panel._open_library()
                dialog = self.panel._library_dialog
                with patch.object(dialog, "_file_dialog", side_effect=lambda _title, _save, handler: handler(str(backup))):
                    dialog._import()
                self.assertEqual(second_store.load()[0]["syeonRevisions"], ["첫 번째 추가 조건", "두 번째 추가 조건"])
                dialog.entries.setCurrentRow(0)
                dialog._restore()
                dialog._dialogs[-1].done(int(QMessageBox.StandardButton.Yes))
                self.app.processEvents()
            self.assertEqual(self.panel.original, "원래 요청")
            self.assertEqual(self.panel.revisions, ["첫 번째 추가 조건", "두 번째 추가 조건"])
            self._submit_offline("세 번째 추가 조건")
            self.assertEqual(self.panel.revisions, ["첫 번째 추가 조건", "두 번째 추가 조건", "세 번째 추가 조건"])
            self.assertIn("이전 결과 직접 편집", self.panel.result.toPlainText())
            self.assertIn("첫 번째 추가 조건", self.panel.result.toPlainText())
            self.assertIn("두 번째 추가 조건", self.panel.result.toPlainText())
            self.assertIn("세 번째 추가 조건", self.panel.result.toPlainText())

    def test_external_details_match_captured_request_and_exclude_first_draft(self):
        original = "QA_ORIGINAL <not-html>\n```\nline two"
        draft = "QA_UNSENT_RESULT"
        self.panel.input.setPlainText(original)
        self.panel.result.setPlainText(draft)
        with patch("optimizer_chat.optimize_ai", return_value={"result": "QA_RESULT", "notes": []}) as ai:
            self.panel.submit()
            dialog = self.panel._confirmation
            details = dialog.detailedText()
            self.assertIn(original, details)
            self.assertNotIn(draft, details)
            self.assertEqual(dialog.textFormat(), Qt.TextFormat.PlainText)
            self.assertEqual(dialog.defaultButton(), dialog.button(QMessageBox.StandardButton.No))
            self.assertEqual(dialog.escapeButton(), dialog.button(QMessageBox.StandardButton.No))
            self.assertIn("JarvisClaw", dialog.button(QMessageBox.StandardButton.Yes).text())
            ai.assert_not_called()
            dialog.button(QMessageBox.StandardButton.Yes).click()
            self.assertEqual(dialog.detailedText(), "")
            self._wait_idle()
            self.assertEqual(ai.call_args.args, (original,))
            for key in ("context", "revision", "previous_result"):
                self.assertEqual(ai.call_args.kwargs[key], "")
        self.panel.result_choice.setCurrentIndex(0)
        self.assertEqual(self.panel.result.toPlainText(), draft)

    def test_followup_details_match_old_context_selected_edit_and_request_settings(self):
        self._submit_offline("QA_ORIGINAL")
        self._submit_offline("QA_CONTEXT_ONE")
        self._submit_offline("QA_CONTEXT_TWO")
        self.panel.result_choice.setCurrentIndex(0)
        self.panel.result.setPlainText("QA_PREVIOUS_EDIT <literal>\n```code```")
        self.panel.input.setPlainText("QA_REVISION")
        self.panel.task.setCurrentIndex(1)
        self.panel.detail.setCurrentIndex(2)
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("pollinations-classic"))
        with patch("optimizer_chat.optimize_ai", return_value={"result": "QA_NEW", "notes": []}) as ai:
            self.panel.submit()
            dialog = self.panel._confirmation
            details = dialog.detailedText()
            self.assertIn("Pollinations Classic", details)
            self.assertIn("Pollinations Classic", dialog.button(QMessageBox.StandardButton.Yes).text())
            dialog.button(QMessageBox.StandardButton.Yes).click()
            self._wait_idle()
            self.assertEqual(ai.call_args.args, ("QA_ORIGINAL",))
            expected = {"context": "QA_CONTEXT_ONE\n\nQA_CONTEXT_TWO", "revision": "QA_REVISION",
                        "previous_result": "QA_PREVIOUS_EDIT <literal>\n```code```",
                        "provider": "pollinations-classic", "task": self.panel.task.currentData(),
                        "detail": self.panel.detail.currentData()}
            self.assertEqual(ai.call_args.kwargs, expected)
            for key in ("context", "revision", "previous_result", "task", "detail"):
                self.assertIn(expected[key], details)
            self.assertEqual(details.count("QA_REVISION"), 1)

    def test_escape_and_initial_enter_cancel_without_feedback_or_worker(self):
        feedback = []
        self.panel.task_feedback.connect(feedback.append)
        self.panel.input.setPlainText("QA_CANCEL")
        with patch("optimizer_chat.optimize_ai") as ai, patch("optimizer_chat.threading.Thread") as worker:
            for key in (Qt.Key.Key_Escape, Qt.Key.Key_Return):
                self.panel.submit()
                dialog = self.panel._confirmation
                self.app.processEvents()
                self._key(dialog, key)
                self.assertFalse(dialog.isVisible())
                self.assertEqual(dialog.detailedText(), "")
                self.app.processEvents()
            ai.assert_not_called()
            worker.assert_not_called()
        self.assertEqual(feedback, [])
        self.assertEqual(self.panel.input.toPlainText(), "QA_CANCEL")

    def test_generic_confirmation_does_not_get_external_details_or_labels(self):
        self.panel.input.setPlainText("QA_DRAFT")
        self.panel._new_request()
        dialog = self.panel._confirmation
        self.assertEqual(dialog.detailedText(), "")
        self.assertNotIn("JarvisClaw", dialog.button(QMessageBox.StandardButton.Yes).text())
        dialog.button(QMessageBox.StandardButton.No).click()
        self.assertEqual(self.panel.input.toPlainText(), "QA_DRAFT")

    def test_pending_details_stay_frozen_and_changed_snapshot_never_sends(self):
        self._submit_offline("QA_ORIGINAL")
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("vireonix"))
        self.panel.input.setPlainText("QA_REVISION")
        with patch("optimizer_chat.optimize_ai") as ai:
            for change in (lambda: self.panel.result.setPlainText("QA_CHANGED_RESULT"),
                           lambda: self.panel.detail.setCurrentIndex(0),
                           lambda: self.panel.provider.setCurrentIndex(self.panel.provider.findData("pollinations-classic"))):
                self.panel.submit()
                dialog = self.panel._confirmation
                captured = dialog.detailedText()
                change()
                self.assertEqual(dialog.detailedText(), captured)
                dialog.button(QMessageBox.StandardButton.Yes).click()
                self.assertFalse(self.panel.is_busy)
            ai.assert_not_called()
        self.assertEqual(self.panel.input.toPlainText(), "QA_REVISION")

    def test_sleep_and_busy_changes_after_consent_block_work(self):
        self.panel.input.setPlainText("QA_ORIGINAL")
        with patch("optimizer_chat.optimize_ai") as ai, patch("optimizer_chat.threading.Thread") as worker:
            self.panel.submit()
            self.panel.set_sleep_paused(True)
            self.panel._confirmation.button(QMessageBox.StandardButton.Yes).click()
            self.assertFalse(self.panel.is_busy)
            self.panel.set_sleep_paused(False)
            self.panel.submit()
            self.panel._set_busy(True)
            self.panel._confirmation.button(QMessageBox.StandardButton.Yes).click()
            self.panel._set_busy(False)
            ai.assert_not_called()
            worker.assert_not_called()
        self.assertEqual(self.panel.input.toPlainText(), "QA_ORIGINAL")

    def test_copy_rejected_write_does_not_acknowledge_or_emit_feedback(self):
        self.panel.result.setPlainText("QA_NEW")
        label = self.panel.copy.text()
        status = self.panel.status.text()
        feedback = []
        self.panel.task_feedback.connect(feedback.append)
        with patch.object(QApplication, "clipboard") as clipboard:
            clipboard.return_value.text.return_value = "QA_OLDER"
            self.panel._copy_result()
        self.assertEqual(feedback, [])
        self.assertEqual(self.panel.copy.text(), label)
        self.assertEqual(self.panel.result.toPlainText(), "QA_NEW")
        self.assertNotEqual(self.panel.status.text(), status)

    def test_copy_unavailable_does_not_acknowledge_or_raise(self):
        self.panel.result.setPlainText("QA_NEW")
        label = self.panel.copy.text()
        status = self.panel.status.text()
        feedback = []
        self.panel.task_feedback.connect(feedback.append)
        with patch.object(QApplication, "clipboard") as clipboard:
            clipboard.return_value.setText.side_effect = RuntimeError("QA unavailable")
            self.panel._copy_result()
        self.assertEqual(feedback, [])
        self.assertEqual(self.panel.copy.text(), label)
        self.assertEqual(self.panel.result.toPlainText(), "QA_NEW")
        self.assertNotEqual(self.panel.status.text(), status)

    def test_copy_acknowledges_exact_nonempty_text_without_geometry_or_focus_change(self):
        value = "QA_COPY <plain>\n[빈칸 이름]"
        feedback = []
        self.panel.task_feedback.connect(feedback.append)
        self.panel.result.setPlainText(value)
        self.panel.copy.setFocus()
        self.app.processEvents()
        before = self.panel.copy.geometry()
        with patch.object(QApplication, "clipboard") as clipboard:
            clipboard.return_value.text.return_value = value
            self.panel.copy.click()
            self.app.processEvents()
            clipboard.return_value.setText.assert_called_once_with(value)
            self.assertEqual(self.panel.copy.geometry(), before)
            self.assertEqual(self.app.focusWidget(), self.panel.copy)
            self.assertEqual(self.panel.result.toPlainText(), value)
            self.assertEqual(feedback, ["copied"])
            self.assertIn("1개", self.panel.status.text())
            self.assertNotEqual(self.panel.copy.text(), "결과 복사")
            self.panel.result.clear()
            self.panel.copy.click()
            clipboard.return_value.setText.assert_called_once_with(value)
            self.assertEqual(feedback, ["copied"])
            self.assertEqual(self.panel.copy.text(), "결과 복사")

    def test_copy_reset_on_edits_same_text_history_new_restore_and_result_replacement(self):
        self._submit_offline("QA_ORIGINAL")
        self._submit_offline("QA_REVISION")
        self.panel.result.setPlainText("QA_IDENTICAL")
        self.panel.result_choice.setCurrentIndex(0)
        self.panel.result.setPlainText("QA_IDENTICAL")
        self.panel.copy.click()
        self.panel.result_choice.setCurrentIndex(1)
        self.assertEqual(self.panel.copy.text(), "결과 복사")
        self.panel.copy.click()
        self.panel.result_choice.activated.emit(1)
        self.assertEqual(self.panel.copy.text(), "결과 복사")
        self.panel.copy.click()
        self.panel.result.insertPlainText("QA_EDIT")
        self.assertEqual(self.panel.copy.text(), "결과 복사")
        self.panel.copy.click()
        self._submit_offline("QA_NEW_RESULT")
        self.assertEqual(self.panel.copy.text(), "결과 복사")
        self.panel.copy.click()
        state = self.panel.export_update_state()
        self.panel._clear_request()
        self.assertEqual(self.panel.copy.text(), "결과 복사")
        self.panel.restore_update_state(state)
        self.assertEqual(self.panel.copy.text(), "결과 복사")
        self.panel.copy.click()
        self.panel._apply_library({"prompt": "QA_RESTORED", "result": self.panel.result.toPlainText()})
        self.assertEqual(self.panel.copy.text(), "결과 복사")

    def test_feedback_only_after_completed_work_failure_and_successful_copy(self):
        feedback, completed = [], []
        self.panel.task_feedback.connect(lambda value: feedback.append((value, self.panel.is_busy)))
        self.panel.result_ready.connect(lambda: completed.append(self.panel.result.toPlainText()))
        self.panel.submit()  # Empty validation is not failed work.
        self.assertEqual(feedback, [])
        started, release = threading.Event(), threading.Event()
        def delayed(*_args, **_kwargs):
            started.set()
            if not release.wait(3):
                raise TimeoutError("test worker not released")
            return {"result": "QA_PRIVATE_RESULT", "notes": []}
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("offline"))
        self.panel.input.setPlainText("QA_PRIVATE_ORIGINAL")
        with patch("optimizer_chat.create_offline_prompt", side_effect=delayed):
            try:
                self.panel.submit()
                self.assertTrue(started.wait(2))
                self.assertEqual(feedback, [])
            finally:
                release.set()
            self._wait_idle()
        self.assertEqual(feedback, [("success", False)])
        self.assertEqual(completed, ["QA_PRIVATE_RESULT"])
        self.panel.copy.click()
        self.assertEqual(feedback[-1], ("copied", False))
        self.panel.input.setPlainText("QA_FAILURE")
        with patch("optimizer_chat.threading.Thread") as worker:
            worker.return_value.start.side_effect = RuntimeError("start failed")
            self.panel.submit()
        self.assertEqual(feedback, [("success", False), ("copied", False), ("failure", False)])
        self.assertEqual(completed, ["QA_PRIVATE_RESULT"])
        self.assertEqual(self.panel.input.toPlainText(), "QA_FAILURE")
        self.assertEqual(self.panel.result.toPlainText(), "QA_PRIVATE_RESULT")

    def test_input_local_ctrl_enter_keeps_newlines_and_uses_normal_consent(self):
        self.panel.input.setPlainText("QA_KEYBOARD")
        cursor = self.panel.input.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.panel.input.setTextCursor(cursor)
        with patch("optimizer_chat.optimize_ai") as ai:
            self._key(self.panel.input, Qt.Key.Key_Return, text="\r")
            self.assertEqual(self.panel.input.toPlainText(), "QA_KEYBOARD\n")
            self.assertFalse(hasattr(self.panel, "_confirmation"))
            self._key(self.panel.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
            self.assertTrue(self.panel._confirmation.isVisible())
            self.panel._confirmation.button(QMessageBox.StandardButton.No).click()
            self.app.processEvents()
            self.panel.result.setPlainText("QA_RESULT")
            with patch.object(self.panel, "submit") as submit:
                self._key(self.panel.result, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
                submit.assert_not_called()
            ai.assert_not_called()
        self.panel.provider.setCurrentIndex(self.panel.provider.findData("offline"))
        self._key(self.panel.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        self._wait_idle()
        self.assertEqual(self.panel.original, "QA_KEYBOARD\n")
        self.assertTrue(self.panel.input.tabChangesFocus())
        self.assertTrue(self.panel.result.tabChangesFocus())
        # The offscreen plugin does not reactivate the parent after a modal
        # dialog closes. Establish an active surface before testing traversal.
        self.panel.activateWindow()
        self.app.processEvents()
        self.panel.input.setFocus()
        self.assertEqual(self.app.focusWidget(), self.panel.input)
        self._key(self.panel.input, Qt.Key.Key_Tab)
        self.assertEqual(self.app.focusWidget(), self.panel.send)
        self._key(self.panel.send, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.app.focusWidget(), self.panel.input)

    def test_extra_tools_toggle_keeps_draft_and_supports_keyboard_navigation(self):
        self.panel.input.setPlainText("보존할 요청")
        self.panel.result.setPlainText("보존할 결과")
        self.assertTrue(all(tool.isHidden() for tool in
                            (self.panel.clarify, self.panel.compare, self.panel.diff)))
        self.panel.tools_toggle.setFocus()
        self._key(self.panel.tools_toggle, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertTrue(all(tool.isVisible() for tool in
                            (self.panel.clarify, self.panel.compare, self.panel.diff)))
        self._key(self.panel.tools_toggle, Qt.Key.Key_Tab)
        self.assertEqual(self.app.focusWidget(), self.panel.clarify)
        self._key(self.panel.clarify, Qt.Key.Key_Tab)
        self.assertEqual(self.app.focusWidget(), self.panel.compare)
        self._key(self.panel.compare, Qt.Key.Key_Tab)
        self.assertEqual(self.app.focusWidget(), self.panel.diff)
        self.panel.tools_toggle.setChecked(False)
        self.assertEqual(self.app.focusWidget(), self.panel.tools_toggle)
        self.assertEqual(self.panel.input.toPlainText(), "보존할 요청")
        self.assertEqual(self.panel.result.toPlainText(), "보존할 결과")

    def test_compact_action_rows_wrap_without_losing_controls(self):
        # Deterministic layout geometry only; native DPI/screenshots belong to
        # the parent executor. Force wrapping using the actual child size hints.
        row = None
        for index in range(self.panel.layout().count()):
            candidate = self.panel.layout().itemAt(index).layout()
            if candidate and any(candidate.itemAt(i).widget() is self.panel.template for i in range(candidate.count())):
                row = candidate
                break
        self.assertIsNotNone(row)
        controls = [row.itemAt(i).widget() for i in range(row.count()) if row.itemAt(i).widget()]
        width = max(control.sizeHint().width() for control in controls) + 2
        height = row.heightForWidth(width)
        row.setGeometry(QRect(0, 0, width, height))
        self.assertGreater(height, max(control.sizeHint().height() for control in controls))
        for control in controls:
            self.assertFalse(control.isHidden())
            self.assertGreaterEqual(control.geometry().left(), 0)
            self.assertLessEqual(control.geometry().right(), width)
        for before, after in zip(controls, controls[1:]):
            self.assertFalse(before.geometry().intersects(after.geometry()))


if __name__ == "__main__":
    unittest.main()
