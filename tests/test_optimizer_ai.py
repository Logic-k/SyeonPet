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
        self.assertRegex(data.pop("request_id"), r"\A[0-9a-f]{16}\Z")
        self.assertEqual(data, {"task": "프로그래밍과 개발",
                                "detail": "필요한 조건과 결과물의 구성을 상세히 설명합니다.",
                                "original_request": "원문 30000원\n조건", "context": "맥락",
                                "revision": "짧게", "previous_result": "기존 결과"})
        self.assertIn("원래 요청과 충돌하면 원래 요청을 우선", payload["messages"][0]["content"])
        self.assertIn("'세 문장 이내'로 바꾸거나", payload["messages"][0]["content"])
        conn.close.assert_called_once()

    def test_each_request_gets_a_new_id_so_stored_replies_are_not_reused(self):
        bodies = []
        for _ in range(2):
            remote = io.BytesIO(response("결과"))
            remote.status = 200
            conn = MagicMock()
            conn.getresponse.return_value = remote
            with patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                    patch.object(ai, "_PinnedHTTPSConnection", return_value=conn):
                ai.optimize_ai("같은 합성 원문")
            payload = json.loads(conn.request.call_args.kwargs["body"])
            bodies.append(json.loads(payload["messages"][1]["content"]))
        self.assertNotEqual(bodies[0]["request_id"], bodies[1]["request_id"])
        self.assertEqual(bodies[0]["original_request"], bodies[1]["original_request"])

    def test_first_rewrite_reattaches_lost_numbers_but_revision_does_not(self):
        for revision, appended in (("", True), ("숫자는 빼 줘", False)):
            remote = io.BytesIO(response("```text\n학교별 합계를 구하는 방법을 알려 주세요.\n- 학교: [학교 이름]\n```"))
            remote.status = 200
            conn = MagicMock()
            conn.getresponse.return_value = remote
            with self.subTest(revision=revision), \
                    patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                    patch.object(ai, "_PinnedHTTPSConnection", return_value=conn):
                reply = ai.optimize_ai("합성 요청: 학교 4개 합계", revision=revision,
                                       previous_result="이전 결과" if revision else "")
            self.assertTrue(reply["result"].startswith("학교별 합계를"))
            self.assertNotIn("```", reply["result"])
            self.assertEqual(reply["result"].endswith("\n합성 요청: 학교 4개 합계"), appended)
            self.assertEqual(any("원문 그대로 붙였어요" in note for note in reply["notes"]), appended)
            self.assertTrue(any("[대괄호]" in note for note in reply["notes"]))

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
        conn.getresponse.return_value.status = 400
        with patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                patch.object(ai, "_PinnedHTTPSConnection", return_value=conn):
            with self.assertRaises(ai.OptimizationError) as raised:
                ai.optimize_ai("private-data")
        self.assertIn("400", str(raised.exception))
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

    def test_temporary_retry_delay_backs_off_and_respects_short_retry_after(self):
        for status, header, attempt, expected in (
                (502, None, 0, 1), (502, None, 1, 2), (429, None, 0, 1), (429, None, 1, 2),
                (503, "0", 0, 1), (504, " 2 ", 1, 2), (502, "3", 0, 3), (429, "3", 1, 3),
                (500, "1", 0, None), (400, None, 0, None), (429, "4", 0, None),
                (503, "4", 0, None), (503, "-1", 0, None), (503, "1.5", 0, None),
                (503, "", 0, None), (503, "9" * 5000, 0, None),
                (503, "Wed, 21 Oct 2026 07:28:00 GMT", 0, None)):
            with self.subTest(status=status, header=repr(header)[:30], attempt=attempt):
                self.assertEqual(ai._temporary_retry_delay(status, header, attempt), expected)

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
            result = ai.optimize_ai("합성 원문", provider="vireonix")
        self.assertEqual(result["result"], "재작성 결과")
        self.assertEqual(factory.call_count, 2)
        resolve.assert_called_once_with("vireonix.ai")
        sleep.assert_called_once_with(1)
        self.assertEqual(first.request.call_args, second.request.call_args)
        initial.close.assert_called_once()
        self.assertTrue(success.closed)
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_retries_stop_after_three_attempts_with_growing_waits(self):
        for header, waits in ((None, [1, 2]), ("2", [2, 2])):
            connections = [MagicMock() for _ in range(3)]
            for connection in connections:
                connection.getresponse.return_value.status = 503
                connection.getresponse.return_value.getheader.return_value = header
            with self.subTest(header=header), \
                    patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                    patch.object(ai, "_PinnedHTTPSConnection", side_effect=connections) as factory, \
                    patch.object(ai.time, "sleep") as sleep:
                with self.assertRaisesRegex(ai.OptimizationError, "503"):
                    ai.optimize_ai("합성 원문")
            self.assertEqual(factory.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], waits)
            for connection in connections:
                connection.request.assert_called_once()
                connection.close.assert_called_once()

    def test_vireonix_rate_limit_is_retried_and_explains_shared_limit(self):
        limited = MagicMock()
        limited.status = 429
        limited.getheader.return_value = None
        success = io.BytesIO(response("재작성 결과"))
        success.status = 200
        first, second = MagicMock(), MagicMock()
        first.getresponse.return_value = limited
        second.getresponse.return_value = success
        with patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                patch.object(ai, "_PinnedHTTPSConnection", side_effect=[first, second]), \
                patch.object(ai.time, "sleep") as sleep:
            self.assertEqual(ai.optimize_ai("합성 원문")["result"], "재작성 결과")
        sleep.assert_called_once_with(1)
        connections = [MagicMock() for _ in range(3)]
        for connection in connections:
            connection.getresponse.return_value.status = 429
            connection.getresponse.return_value.getheader.return_value = None
        with patch.object(ai, "_resolve_public", return_value="8.8.8.8"), \
                patch.object(ai, "_PinnedHTTPSConnection", side_effect=connections), \
                patch.object(ai.time, "sleep"):
            with self.assertRaisesRegex(ai.OptimizationError, "함께 씁니다"):
                ai.optimize_ai("합성 원문")

    def test_retry_is_not_used_for_other_responses_or_provider(self):
        for provider, status, header in (("vireonix", 429, "5"),
                                          ("vireonix", 500, None),
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
