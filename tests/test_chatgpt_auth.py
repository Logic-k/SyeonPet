"""Synthetic OAuth/JWT/HTTP/SSE proofs; never contacts an OpenAI service."""
import base64
import hashlib
import http.client
import io
import json
import multiprocessing
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import ai_secrets
import chatgpt_auth as auth

# Disposable generated fixture. These numbers are not account credentials.
N = ("w8SupfXc5xjR4uEvE4WCATWMyO_BCiGMHiiZOBoaCGmnzjwcjaR-xsiz8iO_d0trvBJNdANwul1PGZSP-JNhKA5VHdMtHXv7Fe8VKTXBX04qVX_HscuLK8b_jFnILtOoDhqLdddN_eF_wAiU6Q0uNig9uiq0YrMviQHb50y08Tf864nrXp9mVk0FLDWg7szKkhEUrpcofZfriQg0h59A1jTjpOcMG3FU4660uczlVbPbTqU0VxpFcjP2riJKcAO8dQ0ektF2fADYYMkPLgFAFEaXj3xmIlYCZC8I9fR9oO90B2gb1zpYyCkUjPm1yAtGVG9OlqJp6ga4lIQb7jdxNw")
D = ("RHa622h2Bk3sTQuSgqG8uv6Gy1jpqkjcL0nkd7XNJ-cHW_0ywShne86B--sbkTVLqpy-AJYZbNuoB5qolPa50h_dDY6mZvrObzfLLzvQ8issa_VBmcDE3Zz9DxDxDMtRqQkIf8A50tFzc22s4ioNYLxHBh3cMu5Wb_Q5-xSc7QSaENXUqFrZLwwibHnmUWFzwEplx__teCtnGWjs3NRaFWu6i6uUQJf65CvK3sNvwfRZWpsrOrCtLvrv0k_eVmgL3oM3GxPOLfDYDDpe43q8p9Iw0c_NF5oUSGb5Cl-gvwRfC8nDDIoMok-f_w0qj2HncYYC_t_ZbAgstETyo0aJSQ")


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def integer(value):
    return int.from_bytes(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)), "big")


def signed_token(nonce="nonce", **changes):
    claims = dict(iss=auth.ISSUER, aud="issued-client", sub="synthetic-subject",
                  exp=time.time() + 3600, nonce=nonce, name="Synthetic Account")
    claims.update(changes)
    head = b64(json.dumps({"alg": "RS256", "kid": "synthetic-key"}).encode())
    payload = b64(json.dumps(claims).encode())
    message = (head + "." + payload).encode()
    info = bytes.fromhex("3031300d060960864801650304020105000420")
    info += hashlib.sha256(message).digest()
    encoded = b"\0\1" + b"\xff" * (256 - len(info) - 3) + b"\0" + info
    signature = pow(int.from_bytes(encoded, "big"), integer(D), integer(N)).to_bytes(256, "big")
    return head + "." + payload + "." + b64(signature)


JWKS = {"keys": [{"kid": "synthetic-key", "kty": "RSA", "alg": "RS256",
                   "use": "sig", "n": N, "e": "AQAB"}]}


def tokens(nonce="nonce", **changes):
    result = dict(access_token="synthetic-access", refresh_token="synthetic-refresh",
                  token_type="Bearer", expires_in=3600, scope=auth.SCOPES,
                  id_token=signed_token(nonce))
    result.update(changes)
    return result


def record():
    result = auth._tokens(tokens())
    result.update(client_id="issued-client", subject="synthetic-subject",
                  display_name="Synthetic Account", workspace_label="Test workspace")
    return result


def stream(*events, trailing=b""):
    return io.BytesIO(b"".join(b"event: " + event["type"].encode() + b"\n"
                             + b"data: " + json.dumps(event).encode() + b"\n\n"
                             for event in events) + trailing)


def refresh_process(root, entered, release, calls, results, blocked=None):
    """Spawned processes share real DPAPI storage and a named Windows mutex."""
    auth._store = ai_secrets.SecretStore(root)
    if blocked is not None:
        original_dll = auth.ctypes.WinDLL

        def dll(name, **kwargs):
            library = original_dll(name, **kwargs)
            if name == "kernel32":
                original_wait = library.WaitForSingleObject
                original_wait.argtypes = [auth.wintypes.HANDLE, auth.wintypes.DWORD]
                original_wait.restype = auth.wintypes.DWORD

                def wait(handle, milliseconds):
                    result = original_wait(handle, milliseconds)
                    if result == 0x102:
                        blocked.set()
                    return result

                library.WaitForSingleObject = wait
            return library

        auth.ctypes.WinDLL = dll

    def http(method, url, **kwargs):
        if url != auth.TOKEN:
            raise AssertionError("Unexpected endpoint")
        calls.put(kwargs["form"]["refresh_token"])
        entered.set()
        if not release.wait(15):
            raise AssertionError("Refresh release not signaled")
        return tokens(id_token=None)

    def refresh_http(method, url, **kwargs):
        value = http(method, url, **kwargs)
        del value["id_token"]
        return value

    auth._http = refresh_http
    try:
        results.put(("ok", auth._access("test", None)))
    except Exception as exc:
        results.put(("error", type(exc).__name__))


@unittest.skipUnless(os.name == "nt", "Windows DPAPI and named mutex required.")
class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ai_secrets.SecretStore(self.temp.name)
        self.patch = patch.object(auth, "_store", self.store)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.cancel = threading.Event()

    def browser(self, url, *, state=None, client_id="issued-client", error=None):
        query = parse_qs(urlsplit(url).query)
        self.query = query
        callback = urlsplit(query["redirect_uri"][0])
        connection = http.client.HTTPConnection(callback.hostname, callback.port, timeout=3)
        values = dict(state=query["state"][0] if state is None else state, code="synthetic-code")
        if client_id is not None:
            values["client_id"] = client_id
        if error:
            values["error"] = error
        connection.request("GET", callback.path + "?" + urlencode(values))
        response = connection.getresponse()
        status = response.status
        response.read()
        connection.close()
        return status

    def service(self, method, url, **kwargs):
        if url == auth.JWKS:
            return JWKS
        self.assertEqual(url, auth.TOKEN)
        self.exchange = kwargs["form"]
        return tokens(self.query["nonce"][0])

    def assert_listener_closed(self):
        callback = urlsplit(self.query["redirect_uri"][0])
        with self.assertRaises(OSError):
            socket.create_connection((callback.hostname, callback.port), timeout=0.2)

    def test_complete_login_dynamic_registration_pkce_and_metadata_only(self):
        def browser(url):
            self.assertIsNotNone(self.store.get_secret("host"))
            self.assertEqual(self.browser(url), 200)
            return True

        with patch.object(auth, "_http", side_effect=self.service):
            info = auth.login(open_browser=browser, cancel=self.cancel)
        self.assertEqual(set(info.__dataclass_fields__),
                         {"id", "display_name", "workspace_label", "connected", "credential_revision"})
        self.assertTrue(info.connected)
        self.assertEqual(self.query["client_id"], ["dynamic_agent_client"])
        self.assertEqual(self.query["agent_name_hint"], ["SyeonPet"])
        self.assertTrue(self.query["ext_agent_host_id"][0].startswith("urn:uuid:"))
        verifier = self.exchange["code_verifier"].encode()
        self.assertEqual(self.query["code_challenge"], [b64(hashlib.sha256(verifier).digest())])
        self.assertEqual(self.exchange["client_id"], "issued-client")
        self.assertEqual(self.exchange["redirect_uri"], self.query["redirect_uri"][0])
        self.assertEqual(auth.list_accounts(), [info])
        self.assertEqual(auth.account_status(info.id), info)
        self.assert_listener_closed()

    def test_wrong_state_is_rejected_and_cancellation_cleans_listener(self):
        def browser(url):
            self.assertEqual(self.browser(url, state="wrong"), 400)
            self.cancel.set()
        with patch.object(auth, "_http") as http:
            with self.assertRaises(auth.CancelledError):
                auth.login(open_browser=browser, cancel=self.cancel)
            http.assert_not_called()
        self.assertEqual(auth.list_accounts(), [])
        self.assert_listener_closed()

    def test_cancel_after_valid_callback_does_not_exchange_or_save(self):
        def browser(url):
            self.browser(url)
            self.cancel.set()
        with patch.object(auth, "_http") as http:
            with self.assertRaises(auth.CancelledError):
                auth.login(open_browser=browser, cancel=self.cancel)
            http.assert_not_called()
        self.assertEqual(auth.list_accounts(), [])
        self.assert_listener_closed()

    def test_listener_timeout_and_browser_failure_are_bounded_and_cleaned(self):
        def browser(url):
            self.query = parse_qs(urlsplit(url).query)
            return True
        with patch.object(auth, "_LOGIN_TIMEOUT", 0):
            with self.assertRaisesRegex(auth.AuthenticationError, "timed out"):
                auth.login(open_browser=browser, cancel=self.cancel)
        self.assert_listener_closed()
        with self.assertRaisesRegex(auth.AuthenticationError, "browser"):
            auth.login(open_browser=lambda url: browser(url) and False, cancel=self.cancel)
        self.assert_listener_closed()

    def test_expected_browser_failures_are_sanitized_but_bugs_propagate(self):
        for error in (OSError, RuntimeError, ValueError, KeyError, AssertionError):
            def browser(url):
                self.query = parse_qs(urlsplit(url).query)
                raise error("private browser diagnostic")

            expected = auth.AuthenticationError if error in (
                OSError, RuntimeError, ValueError) else error
            with self.subTest(error=error.__name__):
                with self.assertRaises(expected) as caught:
                    auth.login(open_browser=browser, cancel=self.cancel)
                if expected is auth.AuthenticationError:
                    self.assertNotIn("private", str(caught.exception))
                self.assert_listener_closed()

    def test_access_denied_and_missing_issued_client_are_not_exchanged(self):
        for kwargs in ({"error": "access_denied"}, {"client_id": None}):
            with self.subTest(kwargs=kwargs), patch.object(auth, "_http") as http:
                with self.assertRaises(auth.AuthenticationError):
                    auth.login(open_browser=lambda url: self.browser(url, **kwargs),
                               cancel=self.cancel)
                http.assert_not_called()
            self.assert_listener_closed()

    def test_wrong_nonce_and_missing_scope_never_save_account(self):
        for value in (tokens("wrong-nonce"), tokens(scope="openid profile email")):
            def service(method, url, **kwargs):
                if url == auth.JWKS:
                    return JWKS
                response = dict(value)
                if response["scope"] != auth.SCOPES:
                    response["id_token"] = signed_token(self.query["nonce"][0])
                return response
            with patch.object(auth, "_http", side_effect=service):
                with self.assertRaises(auth.AuthenticationError):
                    auth.login(open_browser=self.browser, cancel=self.cancel)
            self.assertEqual(auth.list_accounts(), [])
            self.assert_listener_closed()

    def test_reauthorization_reuses_client_and_validates_identity(self):
        with patch.object(auth, "_http", side_effect=self.service):
            first = auth.login(open_browser=self.browser, cancel=self.cancel)
        host = self.query["ext_agent_host_id"]
        old_nonce = self.query["nonce"]
        with patch.object(auth, "_http", side_effect=self.service):
            second = auth.login(open_browser=lambda url: self.browser(url, client_id=None),
                                cancel=self.cancel, account_id=first.id)
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.credential_revision, 2)
        self.assertEqual(self.query["client_id"], ["issued-client"])
        self.assertNotIn("agent_name_hint", self.query)
        self.assertIn("id_token_hint", self.query)
        self.assertEqual(self.query["ext_agent_host_id"], host)
        self.assertNotEqual(self.query["nonce"], old_nonce)
        with patch.object(auth, "_http") as http:
            with self.assertRaises(auth.AuthenticationError):
                auth.login(open_browser=lambda url: self.browser(url, client_id="different-client"),
                           cancel=self.cancel, account_id=first.id)
            http.assert_not_called()  # callback's unexpected ID rejected before exchange

    def test_jwt_signed_valid_and_adversarial_signatures_claims(self):
        valid = signed_token()
        with patch.object(auth, "_http", return_value=JWKS):
            self.assertEqual(auth._verify_id_token(valid, "issued-client", "nonce")["sub"],
                             "synthetic-subject")
            cases = [
                valid.rsplit(".", 1)[0] + "." + b64(bytes(256)),
                valid.rsplit(".", 1)[0] + "." + b64(bytes(255)),
                valid.rsplit(".", 1)[0] + "." + b64(integer(N).to_bytes(256, "big")),
                signed_token(aud="different"),
                signed_token(iss="https://attacker.invalid"),
                signed_token(exp=0),
                signed_token(exp=float("nan")),
                signed_token(nbf=time.time() + 3600),
                signed_token(nonce="wrong"),
                signed_token(aud=["issued-client", "other"]),
                b64(b'{"alg":"none","kid":"synthetic-key"}') + "." + valid.split(".")[1] + ".AA",
                b64(b'{"alg":"HS256","kid":"synthetic-key"}') + "." + valid.split(".")[1] + ".AA",
            ]
            for token in cases:
                with self.subTest(token=cases.index(token)):
                    with self.assertRaises(auth.AuthenticationError):
                        auth._verify_id_token(token, "issued-client", "nonce")
        # DigestInfo must match completely: a valid RSA signature over an encoding
        # with an extra suffix must not pass a prefix-only verifier.
        head, payload, _ = valid.split(".")
        digest = hashlib.sha256((head + "." + payload).encode()).digest()
        forged = b"\0\1" + b"\xff" * (256 - 3 - len(digest) - 1) + b"\0" + digest + b"\0"
        sig = pow(int.from_bytes(forged, "big"), integer(D), integer(N)).to_bytes(256, "big")
        with patch.object(auth, "_http", return_value=JWKS):
            with self.assertRaises(auth.AuthenticationError):
                auth._verify_id_token(head + "." + payload + "." + b64(sig),
                                      "issued-client", "nonce")

    def test_models_preserve_visibility_order_and_generate_exact_boundary(self):
        self.store.set_secret("account:test", record())
        catalog = {"models": [
            {"slug": "b", "display_name": "B", "visibility": "list"},
            {"slug": "hidden", "display_name": "Hidden", "visibility": "hide"},
            {"slug": "a", "display_name": "A", "visibility": "list"}]}
        with patch.object(auth, "_http", return_value=catalog):
            self.assertEqual(auth.list_models("test"), [{"id": "b", "label": "B"},
                                                        {"id": "a", "label": "A"}])
        with patch.object(auth, "_http", return_value="done") as http:
            self.assertEqual(auth.generate("test", "b", "rewrite", {"text": "\ud55c\uae00"}), "done")
        args, kwargs = http.call_args
        self.assertEqual(args, ("POST", "https://api.openai.com/v1/responses"))
        self.assertEqual(kwargs["body"], dict(model="b", instructions="rewrite",
                         input=[{"role": "user", "content": '{"text": "\ud55c\uae00"}'}],
                         store=False, stream=True, tools=[], tool_choice="none"))
        self.assertIs(kwargs["consume"], auth._sse)

    def test_sse_success_requires_completed_and_rejects_partial_failures(self):
        delta = {"type": "response.output_text.delta", "delta": "partial"}
        completed = {"type": "response.completed", "response": {"status": "completed"}}
        self.assertEqual(auth._sse(stream(delta, completed), None), "partial")
        for terminal in (
                {"type": "response.failed", "response": {"error": {"code": "private-error"}}},
                {"type": "response.failed", "response": []},
                {"type": "response.incomplete"},
                {"type": "error"},
                {"type": "response.completed", "response": {"status": "incomplete"}}):
            with self.subTest(terminal=terminal["type"]), self.assertRaises(auth.ProtocolError):
                auth._sse(stream(delta, terminal), None)
        with self.assertRaises(auth.ProtocolError):
            auth._sse(stream(delta), None)
        with self.assertRaises(auth.ProtocolError):
            auth._sse(stream(trailing=b"data: invalid\n\n"), None)
        with self.assertRaises(auth.ProtocolError):
            auth._sse(stream(trailing=b"x" * 262145), None)
        self.cancel.set()
        with self.assertRaises(auth.CancelledError):
            auth._sse(stream(completed), self.cancel)
        failed = {"type": "response.failed", "response": {
            "error": {"code": "subscription_sharing_usage_limit_exceeded"}}}
        with self.assertRaises(auth.UsageLimitError):
            auth._sse(stream(delta, failed), None)

    def test_cancel_during_save_rolls_back_new_credential(self):
        original = self.store.set_secret
        def save(key, value):
            original(key, value)
            self.cancel.set()
        with patch.object(self.store, "set_secret", side_effect=save):
            with self.assertRaises(auth.CancelledError):
                auth._save_credentials("account:test", record(), self.cancel)
        self.assertIsNone(self.store.get_secret("account:test"))

    def test_cancel_before_refresh_sends_nothing_and_preserves_original(self):
        saved = record()
        saved["expires_at"] = 0
        self.store.set_secret("account:test", saved)
        self.cancel.set()
        with patch.object(auth, "_http") as http:
            with self.assertRaises(auth.CancelledError):
                auth.list_models("test", cancel=self.cancel)
            http.assert_not_called()
        self.assertEqual(self.store.get_secret("account:test"), saved)

    def test_cancel_after_refresh_rotation_commits_before_reporting_and_sends_no_inference(self):
        saved = record()
        saved["expires_at"] = 0
        self.store.set_secret("account:test", saved)
        rotated = tokens(access_token="rotated-access", refresh_token="rotated-refresh")

        def service(method, url, **kwargs):
            self.assertIsNone(kwargs.get("cancel"))
            if url == auth.TOKEN:
                self.cancel.set()
                return rotated
            self.assertEqual(url, auth.JWKS)
            return JWKS

        with patch.object(auth, "_http", side_effect=service) as http:
            with self.assertRaises(auth.CancelledError):
                auth.generate("test", "model", "rewrite", {"text": "synthetic"},
                              cancel=self.cancel)
        self.assertEqual([call.args[1] for call in http.call_args_list],
                         [auth.TOKEN, auth.JWKS])
        current = self.store.get_secret("account:test")
        self.assertEqual(current["refresh_token"], "rotated-refresh")
        self.assertEqual(current["access_token"], "rotated-access")
        self.assertEqual(current["credential_revision"], 2)
        self.assertGreater(current["expires_at"], time.time())
        # A new operation uses the replacement without sending the invalidated
        # refresh token or refreshing a second time.
        with patch.object(auth, "_http", return_value={"models": []}) as http:
            self.assertEqual(auth.list_models("test"), [])
        self.assertEqual(http.call_args.args, ("GET", auth.RESOURCE + "/models"))
        self.assertEqual(http.call_args.kwargs["token"], "rotated-access")

    def test_cancel_during_atomic_refresh_save_never_rolls_back_rotation(self):
        saved = record()
        saved["expires_at"] = 0
        self.store.set_secret("account:test", saved)
        rotated = tokens(access_token="rotated-access", refresh_token="rotated-refresh")
        del rotated["id_token"]
        original = self.store.set_secret

        def save(key, value):
            original(key, value)
            self.cancel.set()

        with patch.object(auth, "_http", return_value=rotated), patch.object(
                self.store, "set_secret", side_effect=save):
            with self.assertRaises(auth.CancelledError):
                auth._access("test", self.cancel)
        current = self.store.get_secret("account:test")
        self.assertEqual(current["refresh_token"], "rotated-refresh")
        self.assertEqual(current["access_token"], "rotated-access")
        self.assertEqual(current["credential_revision"], 2)

    def test_logout_clears_tokens_even_when_remote_revoke_fails_and_retains_mapping(self):
        self.store.set_secret("account:test", record())
        self.store.set_secret("accounts", {"ids": ["test"]})
        with patch.object(auth, "_http", side_effect=auth.NetworkError("Safe failure.")):
            with self.assertRaisesRegex(auth.AuthenticationError, "remote revocation"):
                auth.logout("test")
        retained = self.store.get_secret("account:test")
        self.assertNotIn("access_token", retained)
        self.assertNotIn("refresh_token", retained)
        self.assertNotIn("id_token", retained)
        self.assertEqual(retained["client_id"], "issued-client")
        self.assertFalse(auth.list_accounts()[0].connected)
        self.assertEqual(auth.list_accounts()[0].credential_revision, 2)

    def test_logout_discovers_revocation_and_clears_tokens_after_empty_success(self):
        self.store.set_secret("account:test", record())
        endpoint = auth.ISSUER + "/api/accounts/oauth/revoke"
        with patch.object(auth, "_http", side_effect=[
                {"revocation_endpoint": endpoint}, {}]) as http:
            auth.logout("test")
        self.assertEqual(http.call_args_list[0].args, ("GET", auth.DISCOVERY))
        self.assertEqual(http.call_args_list[1].args, ("POST", endpoint))
        self.assertEqual(http.call_args_list[1].kwargs["form"],
                         dict(token="synthetic-refresh", token_type_hint="refresh_token",
                              client_id="issued-client"))
        self.assertFalse(auth.account_status("test").connected)

    def test_refresh_serialized_across_real_spawned_processes(self):
        saved = record()
        saved["expires_at"] = 0
        self.store.set_secret("account:test", saved)
        ctx = multiprocessing.get_context("spawn")
        entered, release, blocked = ctx.Event(), ctx.Event(), ctx.Event()
        calls, results = ctx.Queue(), ctx.Queue()
        first = ctx.Process(target=refresh_process,
                            args=(self.temp.name, entered, release, calls, results))
        second = ctx.Process(target=refresh_process,
                             args=(self.temp.name, entered, release, calls, results, blocked))
        self.addCleanup(lambda: first.is_alive() and first.terminate())
        self.addCleanup(lambda: second.is_alive() and second.terminate())
        first.start()
        self.assertTrue(entered.wait(15))
        second.start()
        # Subscribe before starting the contender; release only after the real
        # Windows wait reports contention. No sleep or scheduling assumption.
        self.assertTrue(blocked.wait(15))
        release.set()
        self.assertEqual(results.get(timeout=20), ("ok", "synthetic-access"))
        self.assertEqual(results.get(timeout=20), ("ok", "synthetic-access"))
        first.join(10)
        second.join(10)
        self.assertEqual((first.exitcode, second.exitcode), (0, 0))
        self.assertEqual(calls.get(timeout=2), "synthetic-refresh")
        self.assertTrue(calls.empty())
        self.assertEqual(self.store.get_secret("account:test")["credential_revision"], 2)


class HTTPBoundaryTests(unittest.TestCase):
    def test_event_stream_media_type_is_normalized_and_failures_are_sanitized(self):
        class Connection:
            sock = None
            content_type = ""

            def __init__(self, *args, **kwargs):
                pass

            def request(self, *args, **kwargs):
                pass

            def close(self):
                pass

            def getresponse(self):
                response = io.BytesIO(b"PRIVATE-RESPONSE")
                response.status = 200
                response.getheader = lambda name, default="": self.content_type
                return response

        with patch.object(auth, "_PublicHTTPS", Connection):
            for header in ("text/event-stream", "Text/Event-Stream; charset=utf-8",
                           "  text/event-stream  ; charset=utf-8"):
                with self.subTest(header=header):
                    Connection.content_type = header
                    self.assertEqual(auth._http("POST", auth.RESOURCE + "/responses",
                                               consume=lambda response, cancel: "ok"), "ok")
            for header, expected in (("application/json", "JSON"),
                                     ("text/html; charset=utf-8", "HTML"),
                                     ("", "missing Content-Type"),
                                     ("PRIVATE-HEADER", "other Content-Type")):
                with self.subTest(header=header):
                    Connection.content_type = header
                    with self.assertRaises(auth.ProtocolError) as caught:
                        auth._http("POST", auth.RESOURCE + "/responses",
                                   consume=lambda response, cancel: "must not be called")
                    self.assertIn(expected, str(caught.exception))
                    self.assertNotIn("PRIVATE", str(caught.exception))

    def test_fixed_https_hosts_no_redirect_and_sanitized_status(self):
        for url in ("http://api.openai.com/v1/models", "https://evil.invalid/",
                    "https://auth.openai.com.evil.invalid/", "https://user@auth.openai.com/",
                    "https://auth.openai.com:444/"):
            with self.subTest(url=url), self.assertRaises(auth.ProtocolError):
                auth._validated_url(url)
        for status, error in ((302, auth.NetworkError), (401, auth.AuthenticationError),
                              (403, auth.AuthenticationError), (429, auth.UsageLimitError),
                              (500, auth.NetworkError)):
            class Connection:
                sock = None
                def __init__(self, *args, **kwargs):
                    pass
                def request(self, *args, **kwargs):
                    pass
                def close(self):
                    pass
                def getresponse(self):
                    response = io.BytesIO(b"PRIVATE-SERVICE-ERROR")
                    response.status = status
                    return response
            with patch.object(auth, "_PublicHTTPS", Connection):
                with self.assertRaises(error) as caught:
                    auth._http("GET", auth.JWKS)
                self.assertNotIn("PRIVATE", str(caught.exception))

    def test_public_dns_boundary_rejects_private_addresses_before_connect(self):
        connection = auth._PublicHTTPS("auth.openai.com", timeout=1)
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch.object(socket, "getaddrinfo", return_value=private):
            with self.assertRaises(auth.NetworkError):
                connection.connect()

    def test_dns_cancellation_is_bounded_without_waiting_for_resolver(self):
        entered, release = threading.Event(), threading.Event()
        connection = auth._PublicHTTPS("auth.openai.com", timeout=1)
        connection.abort = threading.Event()
        outcome = []

        def lookup(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("DNS fixture was not released")
            return []

        def connect():
            try:
                connection.connect()
            except Exception as exc:
                outcome.append(exc)

        with patch.object(socket, "getaddrinfo", side_effect=lookup):
            thread = threading.Thread(target=connect)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                connection.abort.set()
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertIsInstance(outcome[0], auth.NetworkError)
            finally:
                release.set()
                thread.join(3)

    def test_cancel_closes_live_http_connection_and_returns_typed_exception(self):
        entered, closed = threading.Event(), threading.Event()
        cancel = threading.Event()
        outcome = []
        class Connection:
            sock = None
            def __init__(self, *args, **kwargs):
                pass
            def request(self, *args, **kwargs):
                pass
            def getresponse(self):
                entered.set()
                if not closed.wait(5):
                    raise AssertionError("Cancelled connection was not closed")
                raise OSError("private request content")
            def close(self):
                closed.set()
        def request():
            try:
                auth._http("GET", auth.JWKS, cancel=cancel)
            except Exception as exc:
                outcome.append(exc)
        with patch.object(auth, "_PublicHTTPS", Connection):
            thread = threading.Thread(target=request)
            thread.start()
            self.assertTrue(entered.wait(3))
            cancel.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(closed.is_set())
        self.assertIsInstance(outcome[0], auth.CancelledError)


if __name__ == "__main__":
    unittest.main()
