"""Network-free tests for the update check and its GUI-thread contract."""
from __future__ import annotations

import io
import http.client
import json
import socket
import threading
import time
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication

import update_checker as checker


def release(tag="v99.0.0", url=None, **overrides):
    data = {"tag_name": tag, "draft": False, "prerelease": False,
            "html_url": url if url is not None else checker.RELEASE_BASE + tag}
    data.update(overrides)
    return data


class UpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_version_order_stable_only(self):
        self.assertEqual(checker.parse_version("v1.12.0"), (1, 12, 0))
        for text in ("v1.0.0-beta", "1.2", "v01.2.0", "v1.0.0/../../x", "v1.0.0\n", 123):
            with self.subTest(text=text), self.assertRaises(ValueError):
                checker.parse_version(text)

    def test_new_current_and_older(self):
        self.assertEqual(checker._parse_release(release("v99.0.0"))["status"], "available")
        self.assertEqual(checker._parse_release(release("v" + checker.APP_VERSION))["status"], "current")
        self.assertEqual(checker._parse_release(release("v0.0.0"))["status"], "current")

    def test_nonofficial_url_or_prerelease_is_rejected(self):
        bad = [release(url="https://evil.example/update"),
               release(url=checker.RELEASE_BASE + "v99.0.0?token=x"),
               release(draft=True), release(prerelease=True),
               release(url="https://github.com/Other/SyeonPet/releases/tag/v0.1.1")]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                checker._parse_release(value)

    def test_fetch_fixed_url_and_bounded_response(self):
        opener = MagicMock()
        opener.open.return_value = io.BytesIO(json.dumps(release()).encode())
        with patch.object(checker.urllib.request, "build_opener", return_value=opener):
            result = checker.check_latest()
        self.assertEqual(result["status"], "available")
        self.assertEqual(opener.open.call_args.args[0].full_url, checker.API_URL)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], checker._TIMEOUT_SECONDS)
        opener.open.return_value = io.BytesIO(b" " * (checker._MAX_RESPONSE + 1))
        with patch.object(checker.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(checker.check_latest()["status"], "unavailable")

    def test_no_release_offline_redirect_and_malformed(self):
        for code, expected in ((404, "no_release"), (302, "unavailable"), (403, "unavailable")):
            error = urllib.error.HTTPError(checker.API_URL, code, "error", {}, None)
            opener = MagicMock()
            opener.open.side_effect = error
            with self.subTest(code=code), patch.object(checker.urllib.request, "build_opener", return_value=opener):
                self.assertEqual(checker.check_latest()["status"], expected)
        for error in (urllib.error.URLError("offline"), TimeoutError()):
            opener = MagicMock()
            opener.open.side_effect = error
            with self.subTest(error=error), patch.object(checker.urllib.request, "build_opener", return_value=opener):
                self.assertEqual(checker.check_latest()["status"], "unavailable")
        opener = MagicMock()
        opener.open.return_value = io.BytesIO(b"{")
        with patch.object(checker.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(checker.check_latest()["status"], "unavailable")

    def test_redirect_handler_never_follows(self):
        self.assertIsNone(checker._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example/"))

    def test_dripping_body_has_overall_deadline_and_only_one_network_worker(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        entered = threading.Event()

        def serve():
            try:
                with listener.accept()[0] as client:
                    client.recv(65536)
                    client.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\nConnection: close\r\n\r\n")
                    entered.set()
                    for _ in range(8):
                        time.sleep(.15)
                        client.sendall(b" ")
            except OSError:
                pass
            finally:
                listener.close()

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        connections = []

        class LocalOpener:
            def open(self, _request, timeout):
                conn = http.client.HTTPConnection("127.0.0.1", port=port, timeout=timeout)
                connections.append(conn)
                conn.request("GET", "/synthetic")
                return conn.getresponse()

        try:
            with patch.object(checker, "_TIMEOUT_SECONDS", .5), \
                    patch.object(checker.urllib.request, "build_opener", return_value=LocalOpener()):
                started = time.monotonic()
                self.assertEqual(checker.check_latest()["status"], "unavailable")
                self.assertLess(time.monotonic() - started, 1.2)
                self.assertTrue(entered.is_set())
                self.assertEqual(checker.check_latest()["status"], "unavailable")
                self.assertEqual(len(connections), 1)
            server.join(timeout=3)
            self.assertFalse(server.is_alive())
            deadline = time.monotonic() + 2
            while checker._network_lock.locked() and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertFalse(checker._network_lock.locked())
        finally:
            for conn in connections:
                conn.close()
            listener.close()
            server.join(timeout=3)

    def test_manual_request_joins_pending_background_check(self):
        enter = threading.Event()
        finish = threading.Event()
        results = []
        obj = checker.UpdateChecker()
        obj.completed.connect(results.append)

        def pending():
            enter.set()
            finish.wait(2)
            return {"status": "current", "version": "v0.1.0", "release_url": "", "message": "ok"}

        with patch.object(checker, "check_latest", side_effect=pending) as lookup:
            obj.check()
            self.assertTrue(enter.wait(1))
            obj.check(manual=True)
            finish.set()
            deadline = time.monotonic() + 2
            while not results and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["manual"])

    def test_unexpected_worker_error_releases_check(self):
        obj = checker.UpdateChecker()
        results = []
        obj.completed.connect(results.append)
        with patch.object(checker, "check_latest", side_effect=RuntimeError("failure")):
            obj.check(manual=True)
            deadline = time.monotonic() + 2
            while not results and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
        self.assertEqual(results[0]["status"], "unavailable")
        self.assertTrue(results[0]["manual"])
        self.assertFalse(obj._running)


if __name__ == "__main__":
    unittest.main()
