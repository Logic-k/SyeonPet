"""Narrow cloud boundary mocks and real loopback HTTP contract tests."""
from dataclasses import replace
import http.server
import io
import json
import sys
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

import ai_backends as backends
from ai_profiles import AIProfile, ModelInfo
import optimizer_ai


def choice(text=" output ", finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": text}}]}


def cloud_response(value, status=200):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    response = io.BytesIO(raw)
    response.status = status
    response.length = 0
    return response


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.store = types.SimpleNamespace(get_secret=MagicMock(
            return_value={"api_key": "test-secret-key"}))
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {"ai_secrets": self.store}).start()
        self.resolve = patch.object(optimizer_ai, "_resolve_public",
                                    return_value="8.8.8.8").start()
        self.conn = MagicMock()
        self.conn.sock = None
        self.factory = patch.object(optimizer_ai, "_PinnedHTTPSConnection",
                                    return_value=self.conn).start()
        self.profile = AIProfile("g", "groq", "test-model", free_tier_acknowledged=True)

    def answer(self, value, status=200):
        response = cloud_response(value, status)
        self.conn.getresponse.return_value = response
        return response

    def test_groq_fixed_pinned_endpoint_and_exact_request(self):
        remote = self.answer(choice())
        self.assertEqual(backends.generate(self.profile, "system", {"original_request": "user"}),
                         "output")
        self.resolve.assert_called_once_with("api.groq.com")
        self.assertEqual(self.factory.call_args.args[:2], ("api.groq.com", "8.8.8.8"))
        call = self.conn.request.call_args
        self.assertEqual(call.args, ("POST", "/openai/v1/chat/completions"))
        body = json.loads(call.kwargs["body"])
        self.assertEqual(body, {"model": "test-model", "stream": False, "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": '{"original_request": "user"}'},
        ]})
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer test-secret-key")
        self.store.get_secret.assert_called_once_with("provider:groq")
        self.conn.close.assert_called_once()
        self.assertTrue(remote.closed)

    def test_openrouter_only_explicit_free_no_fallbacks_or_plugins(self):
        self.answer(choice())
        profile = AIProfile("r", "openrouter", "vendor/model:free")
        backends.generate(profile, "system", {"plugins": [{"id": "web"}], "model": "paid"})
        body = json.loads(self.conn.request.call_args.kwargs["body"])
        self.assertEqual(body["model"], "vendor/model:free")
        self.assertEqual(body["provider"], {"allow_fallbacks": False})
        self.assertEqual(body["plugins"], [])
        self.assertEqual(self.conn.request.call_args.args[1], "/api/v1/chat/completions")
        self.conn.reset_mock()
        for model in ("vendor/paid", "vendor/model:free/paid"):
            with self.subTest(model=model), self.assertRaises(backends.BackendError):
                backends.generate(replace(profile, model=model), "system", {})
        self.conn.request.assert_not_called()

    def test_gemini_native_request_and_response(self):
        self.answer({"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "hidden", "thought": True}, {"text": "first "}, {"text": "second"},
        ]}}]})
        profile = replace(self.profile, provider="gemini", model="gemini-test")
        self.assertEqual(backends.generate(profile, "system", {"text": "user"}), "first second")
        call = self.conn.request.call_args
        self.assertEqual(call.args, ("POST", "/v1beta/models/gemini-test:generateContent"))
        self.assertEqual(call.kwargs["headers"]["x-goog-api-key"], "test-secret-key")
        self.assertNotIn("test-secret-key", call.args[1])
        body = json.loads(call.kwargs["body"])
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "system"}]})
        self.assertEqual(body["contents"][0]["role"], "user")

    def test_invalid_empty_filtered_truncated_and_http_failures_are_sanitized(self):
        failures = [
            (b"test-secret-key user prompt", 200),
            (choice(""), 200), (choice("partial", "length"), 200),
            (choice("filtered", "content_filter"), 200),
            ({"choices": []}, 200), ({"error": "test-secret-key"}, 200),
            (choice(), 429), (choice(), 302), (choice(), 500),
            ([], 200),
        ]
        for value, status in failures:
            with self.subTest(value=value, status=status):
                self.conn.reset_mock()
                remote = self.answer(value, status)
                with self.assertRaises(backends.BackendError) as caught:
                    backends.generate(self.profile, "user prompt", {})
                self.assertNotIn("test-secret-key", str(caught.exception))
                self.assertNotIn("user prompt", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertTrue(remote.closed)
                self.conn.close.assert_called_once()
                self.conn.request.assert_called_once()

    def test_connection_failure_sanitizes_exception_and_closes(self):
        self.conn.request.side_effect = OSError("test-secret-key user prompt")
        with self.assertRaises(backends.BackendError) as caught:
            backends.generate(self.profile, "user prompt", {})
        self.assertNotIn("test-secret-key", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.conn.close.assert_called_once()

    def test_unexpected_coding_errors_propagate_and_transport_still_closes(self):
        self.store.get_secret.side_effect = RuntimeError("credential coding bug")
        with self.assertRaisesRegex(RuntimeError, "credential coding bug"):
            backends.generate(self.profile, "system", {})
        self.store.get_secret.side_effect = None
        self.conn.request.side_effect = RuntimeError("transport coding bug")
        with self.assertRaisesRegex(RuntimeError, "transport coding bug"):
            backends.generate(self.profile, "system", {})
        self.conn.close.assert_called_once()
        self.conn.request.side_effect = None
        auth = types.SimpleNamespace(
            generate=MagicMock(side_effect=RuntimeError("delegate coding bug")),
            list_models=MagicMock(side_effect=RuntimeError("discovery coding bug")))
        profile = AIProfile("s", "chatgpt-subscription", "auto", account_id="account-1")
        with patch.dict(sys.modules, {"chatgpt_auth": auth}):
            with self.assertRaisesRegex(RuntimeError, "delegate coding bug"):
                backends.generate(profile, "system", {})
            with self.assertRaisesRegex(RuntimeError, "discovery coding bug"):
                backends.list_models(profile)

    def test_oversized_response_and_request_rejected(self):
        with patch.object(backends, "_MAX_BYTES", 500):
            remote = self.answer(b"x" * 501)
            with self.assertRaises(backends.BackendError):
                backends.generate(self.profile, "system", {})
            self.assertTrue(remote.closed)
            self.conn.reset_mock()
            with self.assertRaises(backends.BackendError):
                backends.generate(self.profile, "x" * 501, {})
            self.conn.request.assert_not_called()

    def test_cancellation_before_work_has_no_network_or_secret_read(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(backends.BackendError, "cancelled"):
            backends.generate(self.profile, "system", {}, cancel=cancelled)
        with self.assertRaisesRegex(backends.BackendError, "cancelled"):
            backends.list_models(self.profile, cancel=cancelled)
        self.factory.assert_not_called()
        self.store.get_secret.assert_not_called()

    def test_missing_key_and_acknowledgment_stop_before_send(self):
        for profile in (replace(self.profile, free_tier_acknowledged=False),
                        replace(self.profile, provider="gemini", free_tier_acknowledged=False)):
            with self.subTest(profile=profile), self.assertRaises(backends.BackendError):
                backends.generate(profile, "system", {})
        self.store.get_secret.return_value = {"api_key": "key\r\nInjection"}
        with self.assertRaises(backends.BackendError):
            backends.generate(self.profile, "system", {})
        self.factory.assert_not_called()

    def test_cloud_model_discovery_filters_paid_and_noncompletion_models(self):
        self.answer({"data": [{"id": "vendor/paid"}, {"id": "vendor/model:free", "name": "Free"}]})
        profile = AIProfile("r", "openrouter", "vendor/model:free")
        self.assertEqual(backends.list_models(profile), [ModelInfo("vendor/model:free", "Free")])
        self.assertEqual(self.conn.request.call_args.args, ("GET", "/api/v1/models"))
        self.answer({"models": [
            {"name": "models/embed-test", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-test", "displayName": "Gemini",
             "supportedGenerationMethods": ["generateContent"]},
        ]})
        self.assertEqual(backends.list_models(replace(self.profile, provider="gemini")),
                         [ModelInfo("gemini-test", "Gemini")])
        self.answer({"data": [{"id": "test-model"}]})
        self.assertEqual(backends.list_models(self.profile), [ModelInfo("test-model", "test-model")])

    def test_bad_model_lists_are_sanitized(self):
        for value in ({"data": None}, {"data": ["secret"]}, {"data": [{}]},
                      {"data": [{"id": ""}]}, {"data": [{}] * 513}):
            with self.subTest(value=str(value)[:60]):
                self.answer(value)
                with self.assertRaises(backends.BackendError):
                    backends.list_models(self.profile)

    def test_gemini_does_not_accept_partial_or_blocked_results(self):
        for reason in ("MAX_TOKENS", "SAFETY", None):
            self.answer({"candidates": [{"finishReason": reason,
                                        "content": {"parts": [{"text": "partial"}]}}]})
            with self.subTest(reason=reason), self.assertRaises(backends.BackendError):
                backends.generate(replace(self.profile, provider="gemini"), "system", {})

    def test_legacy_providers_never_route_through_new_adapters(self):
        for provider in ("vireonix", "pollinations-classic"):
            profile = AIProfile(provider, provider, "auto")
            with self.subTest(provider=provider), self.assertRaises(backends.BackendError):
                backends.generate(profile, "system", {})
            with self.assertRaises(backends.BackendError):
                backends.list_models(profile)
        self.factory.assert_not_called()

    def test_subscription_delegates_account_model_and_cancellation_lazily(self):
        auth = types.SimpleNamespace(generate=MagicMock(return_value=" answer "),
                                     list_models=MagicMock(return_value=[
                                         {"id": "auto", "label": "Automatic"}]))
        cancel = threading.Event()
        profile = AIProfile("s", "chatgpt-subscription", "auto", account_id="account-1")
        with patch.dict(sys.modules, {"chatgpt_auth": auth}):
            self.assertEqual(backends.generate(profile, "system", {"input": "text"}, cancel=cancel),
                             "answer")
            self.assertEqual(backends.list_models(profile, cancel=cancel),
                             [ModelInfo("auto", "Automatic")])
        auth.generate.assert_called_once_with("account-1", "auto", "system",
                                               {"input": "text"}, cancel=cancel)
        auth.list_models.assert_called_once_with("account-1", cancel=cancel)
        self.factory.assert_not_called()
        self.store.get_secret.assert_not_called()

    def test_subscription_errors_are_sanitized_and_account_is_required(self):
        profile = AIProfile("s", "chatgpt-subscription", "auto", account_id="account-1")
        auth = types.SimpleNamespace(generate=MagicMock(side_effect=ValueError("secret prompt")))
        with patch.dict(sys.modules, {"chatgpt_auth": auth}):
            with self.assertRaises(backends.BackendError) as caught:
                backends.generate(profile, "system", {})
            self.assertNotIn("secret prompt", str(caught.exception))
            with self.assertRaises(backends.BackendError):
                backends.generate(replace(profile, account_id=None), "system", {})
        self.assertEqual(auth.generate.call_count, 1)

    def test_subscription_known_error_types_show_fixed_safe_guidance(self):
        profile = AIProfile("s", "chatgpt-subscription", "auto", account_id="account-1")
        names = ("CancelledError", "AuthenticationError", "UsageLimitError",
                 "NetworkError", "ProtocolError")
        auth = types.SimpleNamespace(**{name: type(name, (ValueError,), {}) for name in names})
        for name in names:
            with self.subTest(error=name):
                error = getattr(auth, name)("private prompt, token and response")
                auth.generate = MagicMock(side_effect=error)
                auth.list_models = MagicMock(side_effect=error)
                with patch.dict(sys.modules, {"chatgpt_auth": auth}):
                    for call in (lambda: backends.generate(profile, "system", {}),
                                 lambda: backends.list_models(profile)):
                        with self.assertRaises(backends.BackendError) as caught:
                            call()
                        self.assertIn("ChatGPT", str(caught.exception))
                        self.assertNotIn("private", str(caught.exception))
                        self.assertNotIn("token", str(caught.exception))
                        self.assertNotIn("response", str(caught.exception))


class LoopbackTests(unittest.TestCase):
    """Fixture uses only an ephemeral loopback port; production base stays fixed."""

    def setUp(self):
        self.calls = []
        self.replies = {
            "/api/tags": {"models": [{"name": "local:latest"}]},
            "/api/show": {"capabilities": ["completion"]},
            "/api/chat": {"message": {"content": " local output "}, "done": True,
                          "done_reason": "stop"},
        }
        self.blocked = threading.Event()
        self.release = threading.Event()
        self.block_path = None
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def handle_request(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.calls.append((self.command, self.path,
                                    json.loads(raw) if raw else None))
                value = owner.replies[self.path]
                status, length_extra = 200, 0
                if isinstance(value, tuple):
                    value, status, length_extra = value
                body = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body) + length_extra))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.path == owner.block_path:
                    self.wfile.flush()
                    owner.blocked.set()
                    owner.release.wait(15)
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                except (OSError, ConnectionError):
                    pass

            do_GET = handle_request
            do_POST = handle_request

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.base_patch = patch.object(backends, "_OLLAMA_BASE",
                                       f"http://127.0.0.1:{self.server.server_port}")
        self.base_patch.start()
        self.addCleanup(self.cleanup_server)
        self.profile = AIProfile("local", "ollama", "local:latest",
                                 ollama_no_cloud_acknowledged=True)

    def cleanup_server(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(5)
        self.base_patch.stop()
        self.assertFalse(self.server_thread.is_alive())

    def test_discovery_show_and_chat_use_real_no_proxy_http(self):
        with patch.dict("os.environ", {"HTTP_PROXY": "http://unusable.invalid:9",
                                       "HTTPS_PROXY": "http://unusable.invalid:9",
                                       "ALL_PROXY": "http://unusable.invalid:9"}):
            self.assertEqual(backends.list_models(self.profile),
                             [ModelInfo("local:latest", "local:latest")])
            self.assertEqual(backends.generate(self.profile, "system", {"input": "user"}),
                             "local output")
        self.assertEqual([path for _, path, _ in self.calls],
                         ["/api/tags", "/api/show", "/api/show", "/api/chat"])
        self.assertEqual(self.calls[-1][2]["stream"], False)
        self.assertEqual(self.calls[-1][2]["model"], "local:latest")
        self.assertEqual(self.calls[-1][2]["messages"][0]["content"], "system")

    def test_discovery_does_not_require_a_preselected_model(self):
        models = backends.list_models(replace(self.profile, model=""))
        self.assertEqual(models, [ModelInfo("local:latest", "local:latest")])
        self.assertEqual([path for _, path, _ in self.calls], ["/api/tags", "/api/show"])
        with self.assertRaises(backends.BackendError):
            backends.generate(replace(self.profile, model=""), "system", {})

    def test_generation_reinspects_remote_metadata_before_each_chat(self):
        self.assertEqual(backends.generate(self.profile, "system", {}), "local output")
        for metadata in ({"remote_host": "cloud.invalid"}, {"remote_model": "remote"},
                         {"details": {"remote_host": "cloud.invalid"}}):
            self.replies["/api/show"] = {"capabilities": ["completion"], **metadata}
            with self.subTest(metadata=metadata), self.assertRaises(backends.BackendError):
                backends.generate(self.profile, "system", {})
        self.assertEqual(sum(path == "/api/chat" for _, path, _ in self.calls), 1)

    def test_discovery_rejects_cloud_tags_remote_metadata_and_embedding_only(self):
        self.replies["/api/tags"] = {"models": [
            {"name": "remote:cloud"}, {"name": "remote-cloud:latest"},
            {"name": "hidden:latest", "remote_host": "cloud.invalid"},
            {"name": "embedding:latest"},
        ]}
        self.replies["/api/show"] = {"capabilities": ["embedding"]}
        self.assertEqual(backends.list_models(self.profile), [])
        self.assertEqual([path for _, path, _ in self.calls], ["/api/tags", "/api/show"])
        with self.assertRaises(backends.BackendError):
            backends.generate(self.profile, "system", {})
        self.assertNotIn("/api/chat", [path for _, path, _ in self.calls])

    def test_missing_capability_ack_and_cloud_names_stop_before_chat(self):
        for info in ({}, {"capabilities": []}, {"capabilities": "completion"}):
            self.replies["/api/show"] = info
            with self.subTest(info=info), self.assertRaises(backends.BackendError):
                backends.generate(self.profile, "system", {})
        for profile in (replace(self.profile, ollama_no_cloud_acknowledged=False),
                        replace(self.profile, model="remote:cloud")):
            with self.subTest(profile=profile), self.assertRaises(backends.BackendError):
                backends.generate(profile, "system", {})
        self.assertNotIn("/api/chat", [path for _, path, _ in self.calls])

    def test_real_parser_malformed_empty_partial_rate_limit_and_truncated_body(self):
        failures = [
            b"not-json secret prompt", {},
            {"message": {"content": ""}, "done": True},
            {"message": {"content": "partial"}, "done": False},
            {"message": {"content": "partial"}, "done": True, "done_reason": "length"},
            (b"secret prompt", 429, 0),
            ({"message": {"content": "not-complete"}, "done": True}, 200, 10),
        ]
        for response in failures:
            self.replies["/api/chat"] = response
            with self.subTest(response=response), self.assertRaises(backends.BackendError) as caught:
                backends.generate(self.profile, "secret prompt", {})
            self.assertNotIn("secret prompt", str(caught.exception))
        self.replies["/api/chat"] = {"message": {"content": "recovered"}, "done": True}
        self.assertEqual(backends.generate(self.profile, "system", {}), "recovered")

    def test_cancel_interrupts_stalled_response_using_events(self):
        self.block_path = "/api/chat"
        cancelled = threading.Event()
        completed = threading.Event()
        failures = []
        def run():
            try:
                backends.generate(self.profile, "system", {}, cancel=cancelled)
            except backends.BackendError as exc:
                failures.append(str(exc))
            finally:
                completed.set()

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        try:
            self.assertTrue(self.blocked.wait(5), "request did not reach stalled fixture")
            cancelled.set()
            self.assertTrue(completed.wait(5), "cancel did not interrupt response")
            self.assertEqual(failures, ["AI request cancelled."])
        finally:
            cancelled.set()
            self.release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(any(t.name == "ai-backend-cancel" for t in threading.enumerate()))

    def test_absolute_deadline_interrupts_stalled_response(self):
        self.block_path = "/api/chat"
        with patch.object(backends, "_TIMEOUT", .2):
            with self.assertRaisesRegex(backends.BackendError, "deadline"):
                backends.generate(self.profile, "system", {})
        self.assertTrue(self.blocked.is_set())

    def test_base_override_still_rejects_nonloopback_paths_and_credentials(self):
        for base in ("http://localhost:11434", "https://127.0.0.1:11434",
                     "http://127.0.0.1:11434/path", "http://user@127.0.0.1:11434",
                     "http://127.0.0.1:11434?url=elsewhere"):
            with self.subTest(base=base), patch.object(backends, "_OLLAMA_BASE", base):
                with self.assertRaises(backends.BackendError):
                    backends.generate(self.profile, "system", {})
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
