"""SyeonPet OAuth Transport & Network Boundary.

Strict stdlib-only network operations for ChatGPT OAuth and inference:
Public HTTPS pinning, streaming SSE decoding, and sanitized protocol handling.
"""
from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlencode, urlsplit

_TIMEOUT = 90
_MAX_BYTES = 4_000_000


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


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise CancelledError("ChatGPT operation cancelled.")


def _json(data):
    try:
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError, TypeError):
        raise ProtocolError("Invalid ChatGPT service response.") from None


def _b64(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ProtocolError("Invalid ChatGPT identity token.")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError:
        raise ProtocolError("Invalid ChatGPT identity token.") from None


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


def execute_http_request(method, url, *, form=None, body=None, token=None,
                         cancel=None, consume=None, https_cls=None,
                         timeout=_TIMEOUT, max_bytes=_MAX_BYTES):
    _check_cancel(cancel)
    parsed = _validated_url(url)
    conn_cls = https_cls if https_cls is not None else _PublicHTTPS
    connection = conn_cls(parsed.hostname, timeout=15,
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
        sock = getattr(connection, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()

    def guard():
        deadline = time.monotonic() + timeout
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
                category = {"application/json": "JSON", "text/html": "HTML",
                            "": "missing Content-Type"}.get(media_type, "other Content-Type")
                raise ProtocolError(f"ChatGPT did not return an event stream ({category}).")
            result = consume(response, cancel)
        else:
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
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
