"""Direct ChatGPT subscription OAuth and text inference, using only stdlib.

All public operations are synchronous: run them outside the GUI thread.
Cancellation accepts threading.Event. It closes local network connections;
it does not promise server-side cancellation. Never log exceptions' causes,
HTTP bodies, callback URLs, or private credential records.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import http.client
import ipaddress
import json
import math
import re
import secrets
import socket
import ssl
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

import ai_secrets

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
JWKS = ISSUER + "/.well-known/jwks.json"
DISCOVERY = ISSUER + "/.well-known/openid-configuration"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
_REQUIRED = set(SCOPES.split())
_TIMEOUT = 90
_LOGIN_TIMEOUT = 300
_MAX_BYTES = 4_000_000
_store = ai_secrets


class ChatGPTError(ValueError):
    """Sanitized, user-displayable boundary error."""


class CancelledError(ChatGPTError):
    pass


class AuthenticationError(ChatGPTError):
    pass


class ProtocolError(ChatGPTError):
    pass


class NetworkError(ChatGPTError):
    pass


class UsageLimitError(ChatGPTError):
    pass


@dataclass(frozen=True)
class AccountInfo:
    id: str
    display_name: str
    workspace_label: str
    connected: bool
    credential_revision: int


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise CancelledError("ChatGPT operation cancelled.")


@contextmanager
def _lock(key, cancel=None):
    """Named Windows mutex works between app processes and is crash-released."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    namespace = str(getattr(_store, "root", "SyeonPet"))
    digest = hashlib.sha256((namespace + ":" + key).encode()).hexdigest()
    handle = kernel.CreateMutexW(None, False, "Local\\SyeonPet-" + digest)
    if not handle:
        raise AuthenticationError("Could not lock ChatGPT credentials.")
    acquired = False
    deadline = time.monotonic() + _TIMEOUT
    try:
        while not acquired:
            _check_cancel(cancel)
            result = kernel.WaitForSingleObject(handle, 100)
            if result in (0, 0x80):
                acquired = True
            elif result != 0x102 or time.monotonic() >= deadline:
                raise AuthenticationError("ChatGPT credentials are busy.")
        _check_cancel(cancel)
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def _get(key):
    try:
        return _store.get_secret(key)
    except ai_secrets.SecretError:
        raise AuthenticationError("Could not read ChatGPT credentials.") from None


def _set(key, value):
    try:
        _store.set_secret(key, value)
    except ai_secrets.SecretError:
        raise AuthenticationError("Could not save ChatGPT credentials.") from None


def _save_credentials(key, value, cancel):
    previous = _get(key)
    _check_cancel(cancel)
    _set(key, value)
    if cancel is not None and cancel.is_set():
        if previous is None:
            _store.delete_secret(key)
        else:
            _set(key, previous)
        raise CancelledError("ChatGPT operation cancelled.")


def _json(data):
    try:
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError, TypeError):
        raise ProtocolError("Invalid ChatGPT service response.") from None


def _validated_url(url):
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname in
                 ("auth.openai.com", "api.openai.com") and parsed.port in (None, 443)
                 and not parsed.username and not parsed.password and not parsed.fragment)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ProtocolError("Unsupported ChatGPT service address.")
    return parsed


class _PublicHTTPS(http.client.HTTPSConnection):
    """Resolve once, reject private addresses, connect to the checked address."""

    def connect(self):
        resolved = threading.Event()
        resolution = {}

        def resolve():
            try:
                resolution["addresses"] = socket.getaddrinfo(
                    self.host, 443, type=socket.SOCK_STREAM)
            except OSError:
                resolution["error"] = True
            finally:
                resolved.set()

        threading.Thread(target=resolve, daemon=True).start()
        deadline = time.monotonic() + 15
        while not resolved.wait(0.05):
            if (getattr(self, "abort", None) is not None and self.abort.is_set()
                    or time.monotonic() >= deadline):
                raise NetworkError("ChatGPT service address lookup stopped.")
        addresses = resolution.get("addresses", [])
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global
                                for a in addresses):
            raise NetworkError("ChatGPT service address is unavailable.")
        last = None
        for family, socktype, proto, _, address in addresses:
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(self.timeout)
            self.sock = sock
            try:
                if getattr(self, "abort", None) is not None and self.abort.is_set():
                    raise NetworkError("ChatGPT connection stopped.")
                sock.connect(address)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                if getattr(self, "abort", None) is not None and self.abort.is_set():
                    raise NetworkError("ChatGPT connection stopped.")
                return
            except OSError as exc:
                last = exc
                sock.close()
        raise NetworkError("Could not connect to ChatGPT.") from last


def _http(method, url, *, form=None, body=None, token=None, cancel=None, consume=None):
    _check_cancel(cancel)
    parsed = _validated_url(url)
    connection = _PublicHTTPS(parsed.hostname, timeout=15,
                              context=ssl.create_default_context())
    connection.abort = threading.Event()
    headers = {"Accept": "text/event-stream" if consume else "application/json"}
    data = None
    if form is not None:
        data = urlencode(form).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    done = threading.Event()
    timed_out = threading.Event()

    def close():
        connection.abort.set()
        sock = connection.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()

    def guard():
        deadline = time.monotonic() + _TIMEOUT
        while not done.wait(0.05):
            if cancel is not None and cancel.is_set():
                close()
                return
            if time.monotonic() >= deadline:
                timed_out.set()
                close()
                return

    watcher = threading.Thread(target=guard, daemon=True)
    watcher.start()
    try:
        connection.request(method, parsed.path + ("?" + parsed.query if parsed.query else ""),
                           body=data, headers=headers)
        response = connection.getresponse()
        _check_cancel(cancel)
        if response.status in (401, 403):
            raise AuthenticationError("ChatGPT authorization is required.")
        if response.status == 429:
            raise UsageLimitError("ChatGPT usage is currently limited.")
        if response.status < 200 or response.status >= 300:
            raise NetworkError("ChatGPT service request failed.")
        if consume:
            media_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if media_type != "text/event-stream":
                # Never include an arbitrary response header or body in the error.
                # These fixed categories help diagnose a subsequent synthetic request.
                category = {"application/json": "JSON", "text/html": "HTML",
                            "": "missing Content-Type"}.get(media_type, "other Content-Type")
                raise ProtocolError(f"ChatGPT did not return an event stream ({category}).")
            result = consume(response, cancel)
        else:
            raw = response.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                raise ProtocolError("ChatGPT response exceeded the size limit.")
            result = _json(raw) if raw else {}
        _check_cancel(cancel)
        if timed_out.is_set():
            raise NetworkError("ChatGPT request timed out.")
        return result
    except (OSError, http.client.HTTPException, UnicodeError, ValueError) as exc:
        _check_cancel(cancel)
        if isinstance(exc, ChatGPTError):
            raise
        raise NetworkError("ChatGPT connection failed.") from None
    finally:
        done.set()
        close()
        watcher.join()


def _b64(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ProtocolError("Invalid ChatGPT identity token.")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError:
        raise ProtocolError("Invalid ChatGPT identity token.") from None


def _verify_id_token(token, client_id, nonce, cancel=None):
    """Strict RS256 PKCS#1 v1.5 verification, not JWT payload decoding."""
    try:
        if not isinstance(token, str) or len(token) > 100_000:
            raise ValueError()
        head, payload, signature = token.split(".")
        header, claims = _json(_b64(head)), _json(_b64(payload))
        if header.get("alg") != "RS256" or header.get("crit"):
            raise ValueError()
        keys = _http("GET", JWKS, cancel=cancel).get("keys", [])
        matches = [k for k in keys if k.get("kid") == header.get("kid")
                   and k.get("kty") == "RSA" and k.get("use", "sig") == "sig"
                   and k.get("alg", "RS256") == "RS256"]
        if len(matches) != 1:
            raise ValueError()
        key = matches[0]
        n = int.from_bytes(_b64(key["n"]), "big")
        e = int.from_bytes(_b64(key["e"]), "big")
        if not 2048 <= n.bit_length() <= 8192 or e < 3 or e % 2 == 0:
            raise ValueError()
        size = (n.bit_length() + 7) // 8
        sig = _b64(signature)
        if len(sig) != size or int.from_bytes(sig, "big") >= n:
            raise ValueError()
        digest_info = bytes.fromhex("3031300d060960864801650304020105000420")
        digest_info += hashlib.sha256((head + "." + payload).encode("ascii")).digest()
        expected = b"\0\1" + b"\xff" * (size - len(digest_info) - 3) + b"\0" + digest_info
        actual = pow(int.from_bytes(sig, "big"), e, n).to_bytes(size, "big")
        if not hmac.compare_digest(actual, expected):
            raise ValueError()
        aud = claims.get("aud")
        if not (aud == client_id or isinstance(aud, list) and client_id in aud):
            raise ValueError()
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != client_id:
            raise ValueError()
        now = time.time()
        expiry = claims.get("exp")
        if (claims.get("iss") != ISSUER or type(expiry) not in (int, float)
                or not math.isfinite(expiry) or expiry <= now
                or not isinstance(claims.get("sub"), str) or not claims["sub"]):
            raise ValueError()
        if "nbf" in claims and (type(claims["nbf"]) not in (int, float)
                                or not math.isfinite(claims["nbf"])
                                or claims["nbf"] > now):
            raise ValueError()
        if nonce is not None and (not isinstance(claims.get("nonce"), str)
                                  or not hmac.compare_digest(claims["nonce"], nonce)):
            raise ValueError()
        return claims
    except (ValueError, TypeError, KeyError, OverflowError):
        raise AuthenticationError("ChatGPT identity validation failed.") from None


def _tokens(response, old=None):
    try:
        scope = response.get("scope", "")
        if not isinstance(scope, str) or not _REQUIRED.issubset(scope.split()):
            raise ValueError()
        if response.get("token_type", "").lower() != "bearer":
            raise ValueError()
        expiry = response["expires_in"]
        if type(expiry) not in (int, float) or not 0 < expiry <= 86400:
            raise ValueError()
        for name in ("access_token", "refresh_token"):
            if not isinstance(response.get(name), str) or not response[name]:
                raise ValueError()
        record = dict(old or {})
        record.update({name: response[name] for name in ("access_token", "refresh_token")})
        record.update(scope=scope, expires_at=time.time() + expiry,
                      credential_revision=record.get("credential_revision", 0) + 1)
        if "id_token" in response:
            record["id_token"] = response["id_token"]
        return record
    except (ValueError, TypeError, KeyError):
        raise AuthenticationError("ChatGPT plan permission or credentials are missing.") from None


def _info(account_id, record):
    if not record:
        return AccountInfo(account_id, "", "", False, 0)
    return AccountInfo(account_id, record.get("display_name", "ChatGPT"),
                       record.get("workspace_label", "ChatGPT workspace"),
                       bool(record.get("refresh_token")),
                       record.get("credential_revision", 0))


def account_status(account_id):
    return _info(account_id, _get("account:" + account_id))


def list_accounts():
    index = _get("accounts") or {"ids": []}
    accounts = []
    for account_id in index["ids"]:
        record = _get("account:" + account_id)
        if record is not None:
            accounts.append(_info(account_id, record))
    return accounts


def login(*, open_browser, cancel, account_id=None):
    _check_cancel(cancel)
    selected = _get("account:" + account_id) if account_id is not None else None
    if account_id is not None and not selected:
        raise AuthenticationError("The selected ChatGPT registration is unavailable.")
    with _lock("host", cancel):
        host = _get("host")
        if host is None:
            host = {"id": "urn:uuid:" + str(uuid.uuid4())}
            _set("host", host)
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    callback = {}
    received = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            try:
                values = parse_qs(parsed.query, strict_parsing=True, max_num_fields=20)
                valid = (parsed.path == "/auth/callback" and
                         all(len(v) == 1 for v in values.values()) and
                         hmac.compare_digest(values.get("state", [""])[0], state))
            except ValueError:
                valid = False
                values = {}
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Return to SyeonPet." if valid else b"Invalid callback.")
            if valid and not received.is_set():
                callback.update({k: v[0] for k, v in values.items()})
                received.set()

    try:
        server = HTTPServer(("127.0.0.1", 0), Handler)
    except OSError:
        raise NetworkError("Could not start the ChatGPT sign-in callback.") from None
    server.timeout = 0.1
    # Bound malicious local clients that connect but never send a request.
    original_get_request = server.get_request

    def get_request():
        sock, address = original_get_request()
        sock.settimeout(1)
        return sock, address

    server.get_request = get_request
    redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
    stopped = threading.Event()

    def listen():
        while not stopped.is_set() and not received.is_set():
            server.handle_request()

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = dict(client_id="dynamic_agent_client", agent_name_hint="SyeonPet",
                 ext_agent_host_id=host["id"], response_type="code", redirect_uri=redirect,
                 scope=SCOPES, resource=RESOURCE, state=state, nonce=nonce,
                 code_challenge_method="S256", code_challenge=challenge)
    if selected:
        query["client_id"] = selected["client_id"]
        del query["agent_name_hint"]
        if selected.get("id_token"):
            query["id_token_hint"] = selected["id_token"]
    try:
        _check_cancel(cancel)
        try:
            opened = open_browser(AUTHORIZE + "?" + urlencode(query))
        except ChatGPTError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise AuthenticationError("ChatGPT sign-in could not be started.") from None
        if opened is False:
            raise AuthenticationError("Could not open the ChatGPT sign-in browser.")
        deadline = time.monotonic() + _LOGIN_TIMEOUT
        while not received.wait(0.05):
            _check_cancel(cancel)
            if time.monotonic() >= deadline:
                raise AuthenticationError("ChatGPT sign-in timed out.")
        _check_cancel(cancel)
    finally:
        stopped.set()
        thread.join(timeout=2)
        server.server_close()
    if callback.get("error"):
        raise AuthenticationError("ChatGPT sign-in was not authorized.")
    client_id = callback.get("client_id", selected["client_id"] if selected else None)
    code = callback.get("code")
    if not client_id or client_id == "dynamic_agent_client" or not code:
        raise AuthenticationError("ChatGPT registration was incomplete.")
    if selected and client_id != selected["client_id"]:
        raise AuthenticationError("ChatGPT registration did not match the selected account.")
    response = _http("POST", TOKEN, form=dict(grant_type="authorization_code",
                     client_id=client_id, code=code, code_verifier=verifier,
                     redirect_uri=redirect, resource=RESOURCE), cancel=cancel)
    claims = _verify_id_token(response.get("id_token"), client_id, nonce, cancel)
    if selected and claims["sub"] != selected["subject"]:
        raise AuthenticationError("ChatGPT identity did not match the selected account.")
    record = _tokens(response)
    account_id = hashlib.sha256((claims["sub"] + "\0" + client_id).encode()).hexdigest()
    # Workspace is bound by issued client ID, never inferred from the email.
    record.update(subject=claims["sub"], client_id=client_id,
                  display_name=claims.get("name") or claims.get("email") or "ChatGPT",
                  workspace_label="ChatGPT workspace - " + account_id[:8],
                  id_token=response["id_token"])
    with _lock("account:" + account_id, cancel):
        previous = _get("account:" + account_id)
        if previous:
            record["credential_revision"] = previous["credential_revision"] + 1
        with _lock("index", cancel):
            index = _get("accounts") or {"ids": []}
            _check_cancel(cancel)
            # Index first: interruption cannot leave an undiscoverable credential.
            if account_id not in index["ids"]:
                index["ids"].append(account_id)
                _set("accounts", index)
            _save_credentials("account:" + account_id, record, cancel)
    return _info(account_id, record)


def _access(account_id, cancel):
    with _lock("account:" + account_id, cancel):
        record = _get("account:" + account_id)
        if not record or not record.get("refresh_token"):
            raise AuthenticationError("Connect a ChatGPT account first.")
        if record["expires_at"] > time.time() + 30:
            return record["access_token"]
        _check_cancel(cancel)
        # Once refresh is dispatched the server may invalidate the old token.
        # Finish bounded exchange/validation and atomically commit its replacement
        # before honoring cancellation; never roll a rotating credential back.
        response = _http("POST", TOKEN, form=dict(grant_type="refresh_token",
                         client_id=record["client_id"], refresh_token=record["refresh_token"],
                         resource=RESOURCE), cancel=None)
        updated = _tokens(response, record)
        if "id_token" in response:
            claims = _verify_id_token(response["id_token"], record["client_id"], None)
            if claims["sub"] != record["subject"]:
                raise AuthenticationError("ChatGPT account identity changed.")
        _set("account:" + account_id, updated)
        _check_cancel(cancel)
        return updated["access_token"]


def logout(account_id):
    failure = None
    with _lock("account:" + account_id):
        record = _get("account:" + account_id)
        try:
            if record and record.get("refresh_token"):
                discovery = _http("GET", DISCOVERY)
                endpoint = discovery.get("revocation_endpoint")
                if not isinstance(endpoint, str):
                    raise ProtocolError("ChatGPT revocation is unavailable.")
                parsed = _validated_url(endpoint)
                if parsed.hostname != "auth.openai.com":
                    raise ProtocolError("Unsupported ChatGPT revocation address.")
                _http("POST", endpoint, form=dict(token=record["refresh_token"],
                      client_id=record["client_id"], token_type_hint="refresh_token"))
        except ChatGPTError as exc:
            failure = exc
        finally:
            try:
                if record:
                    retained = {k: record[k] for k in
                                ("subject", "client_id", "display_name", "workspace_label",
                                 "credential_revision") if k in record}
                    retained["credential_revision"] = record.get("credential_revision", 0) + 1
                    _set("account:" + account_id, retained)
            except ai_secrets.SecretError:
                raise AuthenticationError("Could not clear local ChatGPT credentials.") from None
    if failure:
        raise AuthenticationError("Local sign-out completed; remote revocation could not be confirmed.") from None


def list_models(account_id, *, cancel=None):
    response = _http("GET", RESOURCE + "/models",
                     token=_access(account_id, cancel), cancel=cancel)
    models = response.get("models")
    if not isinstance(models, list):
        raise ProtocolError("Invalid ChatGPT model catalog.")
    result = []
    for model in models:
        if not isinstance(model, dict):
            raise ProtocolError("Invalid ChatGPT model catalog.")
        if model.get("visibility") == "list":
            if not all(isinstance(model.get(k), str) and model[k]
                       for k in ("slug", "display_name")):
                raise ProtocolError("Invalid ChatGPT model catalog.")
            result.append({"id": model["slug"], "label": model["display_name"]})
    return result


def _sse(response, cancel):
    total, parts, event, data = 0, [], "", []
    while True:
        _check_cancel(cancel)
        line = response.readline(262145)
        total += len(line)
        if total > _MAX_BYTES or len(line) > 262144:
            raise ProtocolError("ChatGPT stream exceeded the size limit.")
        if not line:
            raise ProtocolError("ChatGPT stream ended before completion.")
        try:
            text = line.decode("utf-8").rstrip("\r\n")
        except UnicodeError:
            raise ProtocolError("Invalid ChatGPT event stream.") from None
        if text == "":
            if not data:
                event = ""
                continue
            message = _json("\n".join(data))
            kind = message.get("type", event)
            if event and kind != event:
                raise ProtocolError("Conflicting ChatGPT event type.")
            if kind == "response.output_text.delta":
                if not isinstance(message.get("delta"), str):
                    raise ProtocolError("Invalid ChatGPT text delta.")
                parts.append(message["delta"])
            elif kind == "response.completed":
                completed = message.get("response")
                if not isinstance(completed, dict) or completed.get("status") != "completed":
                    raise ProtocolError("ChatGPT response was not completed.")
                _check_cancel(cancel)
                return "".join(parts)
            elif kind in ("response.failed", "response.incomplete", "error"):
                response_data = message.get("response", {})
                if not isinstance(response_data, dict):
                    raise ProtocolError("Invalid ChatGPT failure event.")
                error = response_data.get("error") or message.get("error") or {}
                if isinstance(error, dict) and error.get("code") in (
                        "subscription_sharing_usage_limit_exceeded",
                        "subscription_sharing_usage_unavailable"):
                    raise UsageLimitError("ChatGPT subscription usage is unavailable.")
                raise ProtocolError("ChatGPT did not complete the response.")
            event, data = "", []
        elif text.startswith("data:"):
            data.append(text[5:].removeprefix(" "))
        elif text.startswith("event:"):
            event = text[6:].strip()


def generate(account_id, model, instructions, payload, *, cancel=None):
    if not isinstance(model, str) or not model or not isinstance(instructions, str):
        raise ProtocolError("Invalid ChatGPT inference request.")
    try:
        content = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError):
        raise ProtocolError("ChatGPT input must be JSON serializable.") from None
    if len(content.encode("utf-8")) > 1_000_000 or len(instructions) > 100_000:
        raise ProtocolError("ChatGPT input exceeded the size limit.")
    token = _access(account_id, cancel)
    return _http("POST", RESOURCE + "/responses", token=token, cancel=cancel,
                 body=dict(model=model, instructions=instructions,
                           input=[{"role": "user", "content": content}],
                           store=False, stream=True, tools=[], tool_choice="none"),
                 consume=_sse)
