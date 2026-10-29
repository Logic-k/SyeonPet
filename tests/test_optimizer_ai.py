"""Network-free tests for explicit AI requests and response handling."""
import io
import http.client
import json
import socket
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import optimizer_ai as ai


def response(content="결과", finish="stop"):
    return json.dumps({"choices": [{"finish_reason": finish,
                                    "message": {"content": content}}]},
                      ensure_ascii=False).encode("utf-8")


class AiOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.saved_time = ai._poll_last_started
        ai._poll_last_started = None

    def tearDown(self):
        ai._poll_last_started = self.saved_time

    def test_request_preserves_original_and_sends_only_selected_inputs(self):
        remote = io.BytesIO(response("  다듬은 결과  "))
        remote.status = 200
        conn = MagicMock()
        conn.getresponse.return_value = remote
        with patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                patch.object(ai, "_PinnedHTTPSConnection", return_value=conn) as factory:
            result = ai.optimize_ai("원문 30000원\n조건", "맥락", "coding", "detailed",
                                    "vireonix", "짧게", "기존 결과")
        self.assertEqual(result["result"], "다듬은 결과")
        self.assertEqual(result["mode"], "ai")
        self.assertEqual(factory.call_args.args[:2], ("vireonix.ai", "8.8.8.8"))
        self.assertLessEqual(factory.call_args.args[2], 90)
        call = conn.request.call_args
        self.assertEqual(call.args, ("POST", "/v1/chat/completions"))
        self.assertNotIn("Authorization", call.kwargs["headers"])
        payload = json.loads(call.kwargs["body"])
        self.assertEqual(payload["model"], "auto")
        self.assertFalse(payload["stream"])
        data = json.loads(payload["messages"][1]["content"])
        self.assertEqual(data, {"task": "프로그래밍과 개발",
                                "detail": "필요한 조건과 결과물의 구성을 상세히 설명합니다.",
                                "original_request": "원문 30000원\n조건", "context": "맥락",
                                "revision": "짧게", "previous_result": "기존 결과"})
        self.assertIn("원래 요청과 충돌하면 원래 요청을 우선", payload["messages"][0]["content"])
        self.assertIn("'세 문장 이내'로 바꾸거나", payload["messages"][0]["content"])
        conn.close.assert_called_once()

    def test_validation_rejects_unknown_provider_and_utf16_length(self):
        for kwargs in ({"provider": "https://example.test"}, {"prompt": " "},
                       {"context": "😀" * 1001}, {"revision": "😀" * 1001},
                       {"previous_result": "😀" * 15001}, {"task": "other"}):
            args = {"prompt": "hello"}
            args.update(kwargs)
            with self.subTest(kwargs=kwargs.keys()), patch.object(ai, "_send") as send:
                with self.assertRaises(ai.OptimizationError):
                    ai.optimize_ai(**args)
                send.assert_not_called()

    def test_pollinations_cooldown_and_fixed_provider(self):
        with patch.object(ai, "_send", return_value="결과") as send, \
                patch.object(ai.time, "monotonic", side_effect=[100, 110, 130]):
            self.assertEqual(ai.optimize_ai("처음", provider="pollinations-classic")["result"], "결과")
            with self.assertRaisesRegex(ai.OptimizationError, "20초"):
                ai.optimize_ai("두 번째", provider="pollinations-classic")
            self.assertEqual(ai.optimize_ai("세 번째", provider="pollinations-classic")["result"], "결과")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args.args[:2],
                         ("https://text.pollinations.ai/v1/chat/completions", "openai"))

    def test_pollinations_concurrent_request_rejected(self):
        ai._poll_lock.acquire()
        try:
            with patch.object(ai, "_send") as send:
                with self.assertRaisesRegex(ai.OptimizationError, "진행 중"):
                    ai.optimize_ai("요청", provider="pollinations-classic")
                send.assert_not_called()
        finally:
            ai._poll_lock.release()

    def test_invalid_or_incomplete_response_rejected(self):
        values = [response("부분", "length"), response("부분", "content_filter"),
                  response("  "), b"not-json", b'{"choices":["x"]}', b"x" * 2_000_001]
        for data in values:
            with self.subTest(size=len(data)), self.assertRaises(ai.OptimizationError):
                ai._read_result(io.BytesIO(data))
        self.assertEqual(ai._read_result(io.BytesIO(response("완료", None))), "완료")

    def test_http_failure_does_not_retry_and_never_contains_private_content(self):
        conn = MagicMock()
        conn.getresponse.return_value.status = 429
        with patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                patch.object(ai, "_PinnedHTTPSConnection", return_value=conn):
            with self.assertRaises(ai.OptimizationError) as raised:
                ai.optimize_ai("private-data")
        self.assertIn("429", str(raised.exception))
        self.assertNotIn("private-data", str(raised.exception))
        conn.request.assert_called_once()

    def test_redirects_are_not_followed(self):
        conn = MagicMock()
        conn.getresponse.return_value.status = 302
        with patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                patch.object(ai, "_PinnedHTTPSConnection", return_value=conn):
            with self.assertRaisesRegex(ai.OptimizationError, "302"):
                ai.optimize_ai("요청")
        conn.request.assert_called_once()

    def test_temporary_retry_after_rules_match_existing_toolkit(self):
        for status, header, expected in ((502, None, 1), (503, "0", 1),
                                         (504, " 2 ", 2), (502, "3", 3),
                                         (429, None, None), (500, "1", None),
                                         (503, "4", None), (503, "-1", None),
                                         (503, "1.5", None), (503, "", None),
                                         (503, "9" * 5000, None),
                                         (503, "Wed, 21 Oct 2026 07:28:00 GMT", None)):
            with self.subTest(status=status, header=repr(header)[:30]):
                self.assertEqual(ai._temporary_retry_delay(status, header), expected)

    def test_vireonix_retry_uses_identical_body_closes_and_waits_once(self):
        initial = MagicMock()
        initial.status = 502
        initial.getheader.return_value = None
        success = io.BytesIO(response("재작성 결과"))
        success.status = 200
        first, second = MagicMock(), MagicMock()
        first.getresponse.return_value = initial
        second.getresponse.return_value = success
        with patch.object(ai, "_resolve_public", return_value="8.8.8.8") as resolve, \
                patch.object(ai, "_PinnedHTTPSConnection", side_effect=[first, second]) as factory, \
                patch.object(ai.time, "sleep") as sleep:
            result = ai.optimize_ai("합성 원문 3개", provider="vireonix")
        self.assertEqual(result["result"], "재작성 결과")
        self.assertEqual(factory.call_count, 2)
        resolve.assert_called_once_with("vireonix.ai")
        sleep.assert_called_once_with(1)
        self.assertEqual(first.request.call_args, second.request.call_args)
        initial.close.assert_called_once()
        self.assertTrue(success.closed)
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_failed_second_attempt_does_not_retry_again(self):
        first, second = MagicMock(), MagicMock()
        for connection in (first, second):
            connection.getresponse.return_value.status = 503
            connection.getresponse.return_value.getheader.return_value = "2"
        with patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                patch.object(ai, "_PinnedHTTPSConnection", side_effect=[first, second]), \
                patch.object(ai.time, "sleep") as sleep:
            with self.assertRaisesRegex(ai.OptimizationError, "503"):
                ai.optimize_ai("합성 원문")
        sleep.assert_called_once_with(2)
        first.request.assert_called_once()
        second.request.assert_called_once()
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_retry_is_not_used_for_other_responses_or_provider(self):
        for provider, status, header in (("vireonix", 429, "1"),
                                          ("vireonix", 503, "4"),
                                          ("vireonix", 503, "nonsense"),
                                          ("pollinations-classic", 503, "1")):
            conn = MagicMock()
            conn.getresponse.return_value.status = status
            conn.getresponse.return_value.getheader.return_value = header
            with self.subTest(provider=provider, status=status, header=header), \
                    patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                    patch.object(ai, "_PinnedHTTPSConnection", return_value=conn) as factory, \
                    patch.object(ai.time, "sleep") as sleep:
                with self.assertRaises(ai.OptimizationError):
                    ai.optimize_ai("합성 원문", provider=provider)
                self.assertEqual(factory.call_count, 1)
                sleep.assert_not_called()
                conn.close.assert_called_once()

    def test_retry_is_not_started_when_remaining_deadline_is_too_short(self):
        conn = MagicMock()
        conn.getresponse.return_value.status = 502
        conn.getresponse.return_value.getheader.return_value = "3"
        with patch.object(ai, "_TIMEOUT", 0.5), \
                patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                patch.object(ai, "_PinnedHTTPSConnection", return_value=conn) as factory, \
                patch.object(ai.time, "sleep") as sleep:
            with self.assertRaisesRegex(ai.OptimizationError, "502"):
                ai.optimize_ai("합성 원문")
        self.assertEqual(factory.call_count, 1)
        sleep.assert_not_called()
        conn.close.assert_called_once()

    def test_retry_requires_exact_vireonix_endpoint_and_model(self):
        url = ai._PROVIDERS["vireonix"][0]
        for selected_url, model in ((url, "other"),
                                    ("https://vireonix.ai.evil.invalid/v1/chat/completions", "auto"),
                                    ("https://vireonix.ai/v1/other", "auto")):
            conn = MagicMock()
            conn.getresponse.return_value.status = 502
            conn.getresponse.return_value.getheader.return_value = "1"
            with self.subTest(url=selected_url, model=model), \
                    patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                    patch.object(ai, "_PinnedHTTPSConnection", return_value=conn) as factory, \
                    patch.object(ai.time, "sleep") as sleep:
                with self.assertRaisesRegex(ai.OptimizationError, "502"):
                    ai._send(selected_url, model, {"original_request": "합성"}, "vireonix")
                self.assertEqual(factory.call_count, 1)
                sleep.assert_not_called()

    def test_timeout_and_connection_failures_do_not_retry(self):
        for error in (TimeoutError(), OSError("offline")):
            conn = MagicMock()
            conn.request.side_effect = error
            with self.subTest(error=type(error).__name__), \
                    patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                    patch.object(ai, "_PinnedHTTPSConnection", return_value=conn) as factory, \
                    patch.object(ai.time, "sleep") as sleep:
                with self.assertRaises(ai.OptimizationError):
                    ai.optimize_ai("합성 원문")
                self.assertEqual(factory.call_count, 1)
                sleep.assert_not_called()
                conn.close.assert_called_once()

    def test_dns_rejects_mixed_public_private_and_pins_one_result(self):
        def dns(ip):
            return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
        with patch.object(ai.socket, "getaddrinfo", return_value=[dns("8.8.8.8"), dns("1.1.1.1")]):
            self.assertEqual(ai._resolve_public("vireonix.ai"), "8.8.8.8")
        with patch.object(ai.socket, "getaddrinfo", return_value=[dns("8.8.8.8"), dns("127.0.0.1")]):
            with self.assertRaisesRegex(ai.OptimizationError, "사설망"):
                ai._resolve_public("vireonix.ai")

    def test_dns_failure_informs_user_without_leaking_request_or_error(self):
        with patch.object(ai.socket, "getaddrinfo", side_effect=socket.gaierror("private DNS error")), \
                patch.object(ai, "_PinnedHTTPSConnection") as connection:
            with self.assertRaises(ai.OptimizationError) as raised:
                ai.optimize_ai("private request", provider="vireonix")
        message = str(raised.exception)
        self.assertIn("인터넷 연결과 DNS 설정", message)
        self.assertNotIn("private DNS error", message)
        self.assertNotIn("private request", message)
        connection.assert_not_called()

    def test_dns_timeout_informs_user_to_check_connectivity(self):
        # No network or 10-second wait: a resolver worker that does not run
        # and a queue that times out emulate the externally visible failure.
        worker = MagicMock()
        worker.start.side_effect = lambda: ai._dns_lock.release()
        result = MagicMock()
        result.get.side_effect = ai.queue.Empty
        with patch.object(ai.threading, "Thread", return_value=worker), \
                patch.object(ai.queue, "Queue", return_value=result):
            with self.assertRaises(ai.OptimizationError) as raised:
                ai._resolve_public("vireonix.ai")
        self.assertIn("시간이 초과", str(raised.exception))
        self.assertIn("인터넷 연결과 DNS 설정", str(raised.exception))

    def test_tls_connect_uses_pinned_address_and_original_host(self):
        sock = MagicMock()
        wrapped = MagicMock()
        context = MagicMock()
        context.wrap_socket.return_value = wrapped
        with patch.object(ai.ssl, "create_default_context", return_value=context), \
                patch.object(ai.socket, "create_connection", return_value=sock) as connect:
            conn = ai._PinnedHTTPSConnection("vireonix.ai", "8.8.8.8", 15)
            conn.connect()
        self.assertEqual(connect.call_args.args[0], ("8.8.8.8", 443))
        self.assertEqual(context.wrap_socket.call_args.kwargs["server_hostname"], "vireonix.ai")
        wrapped.do_handshake.assert_called_once()

    def test_trickle_response_respects_absolute_deadline(self):
        slow = MagicMock()
        slow.read1.side_effect = [b"x", b"x", b"x"]
        with patch.object(ai.time, "monotonic", side_effect=[1, 2, 10]):
            with self.assertRaisesRegex(ai.OptimizationError, "초과"):
                ai._read_result(slow, deadline=5)
        self.assertEqual(slow.read1.call_count, 2)

    def test_trickle_transport_fails_near_total_deadline(self):
        response_obj = MagicMock()
        response_obj.status = 200

        def drip(_size):
            time.sleep(0.025)
            return b"x"

        response_obj.read1.side_effect = drip
        conn = MagicMock()
        conn.getresponse.return_value = response_obj
        started = time.monotonic()
        with patch.object(ai, "_TIMEOUT", 0.06), \
                patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                patch.object(ai, "_PinnedHTTPSConnection", return_value=conn):
            with self.assertRaisesRegex(ai.OptimizationError, "초과"):
                ai.optimize_ai("요청")
        self.assertLess(time.monotonic() - started, 1)
        conn.request.assert_called_once()
        conn.close.assert_called_once()

    def test_connection_close_stalled_body_is_interrupted_at_deadline(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def serve():
            try:
                with listener.accept()[0] as client:
                    client.recv(65536)
                    client.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 100\r\n\r\nx")
                    time.sleep(0.4)
            finally:
                listener.close()

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        started = time.monotonic()
        with patch.object(ai, "_TIMEOUT", 0.08), \
                patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                patch.object(ai, "_PinnedHTTPSConnection",
                             side_effect=lambda host, ip, timeout: http.client.HTTPConnection(
                                 "127.0.0.1", port=port, timeout=timeout)):
            with self.assertRaisesRegex(ai.OptimizationError, "초과"):
                ai.optimize_ai("local synthetic test")
        self.assertLess(time.monotonic() - started, 0.3)
        server.join(timeout=1)
        self.assertFalse(server.is_alive())

    def test_unwrap_only_unambiguous_vireonix_prompt(self):
        self.assertEqual(ai._unwrap_vireonix('```json\n{"prompt":"안녕하세요"}\n```', "일반 요청"), "안녕하세요")
        for text, original in [('{"prompt":"텍스트"}', "JSON 작성"),
                               ('{"prompt":"텍스트"}', '{"a":1}'),
                               ('{"prompt":"a","prompt":"b"}', "일반 요청"),
                               ('{"prompt":"a","other":1}', "일반 요청")]:
            with self.subTest(text=text, original=original):
                self.assertEqual(ai._unwrap_vireonix(text, original), text)


if __name__ == "__main__":
    unittest.main()
