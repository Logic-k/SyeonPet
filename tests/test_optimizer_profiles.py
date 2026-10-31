"""Profile-backed optimizer integration preserves the existing prompt contract."""
import threading
import unittest
from unittest.mock import patch

import optimizer_ai
from ai_profiles import AIProfile


class ProfileOptimizerTests(unittest.TestCase):
    def test_profile_request_keeps_inputs_and_reports_selected_model(self):
        profile = AIProfile("local", "ollama", "qwen3:4b",
                            ollama_no_cloud_acknowledged=True)
        with patch("ai_backends.generate", return_value="원래 수량 30명 이내") as generate:
            reply = optimizer_ai.optimize_ai("원래 수량 30명 이내", context="직원용",
                                            provider="ollama", profile=profile)
        self.assertEqual(generate.call_args.args[0], profile)
        payload = generate.call_args.args[2]
        self.assertEqual(payload["original_request"], "원래 수량 30명 이내")
        self.assertEqual(payload["context"], "직원용")
        self.assertEqual((reply["provider"], reply["model"]), ("ollama", "qwen3:4b"))
        self.assertEqual(reply["result"], "원래 수량 30명 이내")
        self.assertGreaterEqual(reply["elapsed_ms"], 0)

    def test_missing_or_mismatched_profile_never_dispatches(self):
        wrong = AIProfile("wrong", "openrouter", "vendor/model:free")
        with patch("ai_backends.generate") as generate:
            for profile in (None, wrong):
                with self.subTest(profile=profile), self.assertRaises(optimizer_ai.OptimizationError):
                    optimizer_ai.optimize_ai("합성 요청", provider="ollama", profile=profile)
            generate.assert_not_called()

    def test_canceled_request_and_late_reply_are_not_success(self):
        profile = AIProfile("local", "ollama", "qwen3:4b",
                            ollama_no_cloud_acknowledged=True)
        cancel = threading.Event()
        cancel.set()
        with patch("ai_backends.generate") as generate:
            with self.assertRaises(optimizer_ai.OptimizationError):
                optimizer_ai.optimize_ai("합성 요청", provider="ollama", profile=profile, cancel=cancel)
            generate.assert_not_called()
        cancel.clear()

        def complete_after_cancel(*args, **kwargs):
            cancel.set()
            return "반환하면 안 되는 결과"

        with patch("ai_backends.generate", side_effect=complete_after_cancel):
            with self.assertRaises(optimizer_ai.OptimizationError):
                optimizer_ai.optimize_ai("합성 요청", provider="ollama", profile=profile, cancel=cancel)

    def test_backend_failure_stays_a_user_facing_optimizer_error(self):
        profile = AIProfile("local", "ollama", "qwen3:4b",
                            ollama_no_cloud_acknowledged=True)
        with patch("ai_backends.generate", side_effect=ValueError("연결을 확인하세요")):
            with self.assertRaises(optimizer_ai.OptimizationError):
                optimizer_ai.optimize_ai("합성 요청", provider="ollama", profile=profile)
