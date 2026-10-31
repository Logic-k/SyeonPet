"""Explicit fixed-endpoint adapters. No fallback, retries, proxy, or model pulls.

Synchronous calls belong in a worker thread. ``cancel`` is a threading.Event.
Legacy providers stay in optimizer_ai and are deliberately not dispatched here.
"""
from __future__ import annotations

from dataclasses import replace
import http.client
import json
import socket
import threading
import time
from urllib.parse import quote, urlsplit

from ai_profiles import AIProfile, ModelInfo, validate_profile


_OLLAMA_BASE = "http://127.0.0.1:11434"
_CLOUD_HOSTS = {
    "groq": ("api.groq.com", "/openai/v1"),
    "openrouter": ("openrouter.ai", "/api/v1"),
    "gemini": ("generativelanguage.googleapis.com", "/v1beta"),
}
_TIMEOUT = 90
_MAX_BYTES = 2_000_000
_MAX_MODELS = 512


class BackendError(ValueError):
    """Sanitized error containing no request, credential, or server body."""


def _check(cancel: threading.Event | None, deadline: float) -> None:
    if cancel is not None and cancel.is_set():
        raise BackendError("AI request cancelled.")
    if time.monotonic() >= deadline:
        raise BackendError("AI request deadline exceeded.")


def _validate(profile: AIProfile) -> None:
    try:
        validate_profile(profile)
    except (ValueError, TypeError):
        raise BackendError("Invalid AI profile settings.") from None
    if profile.provider in ("vireonix", "pollinations-classic"):
        raise BackendError("This legacy provider must use optimizer_ai.")


def _secret(provider: str) -> str:
    try:
        from ai_secrets import get_secret
        value = get_secret("provider:" + provider)
        key = value["api_key"]
        if not isinstance(key, str) or not key or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ValueError
        return key
    except (ValueError, OSError, KeyError, TypeError):
        raise BackendError("Configure the provider API key first.") from None


def _request(provider: str, method: str, path: str, body: dict | None,
             headers: dict, cancel: threading.Event | None, deadline: float) -> dict:
    _check(cancel, deadline)
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    if data is not None and len(data) > _MAX_BYTES:
        raise BackendError("AI request is too large.")
    conn = None
    response = None
    active_socket = [None]
    finished = threading.Event()
    timer = None
    watcher = None

    def interrupt():
        sock = (conn.sock if conn is not None else None) or active_socket[0]
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            finally:
                # HTTPResponse.makefile keeps an IO reference, so socket.close()
                # can defer closesocket on Windows and leave its select blocked.
                # This connection is being aborted; close the OS socket now.
                socket.socket._real_close(sock)

    def watch_cancel():
        # Wake on completion as well, so successful calls leave no waiter behind.
        while not finished.wait(.05):
            if cancel.is_set():
                interrupt()
                return

    try:
        if provider == "ollama":
            parsed = urlsplit(_OLLAMA_BASE)
            if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path or parsed.query or parsed.fragment):
                raise BackendError("Invalid Ollama loopback endpoint.")
            conn = http.client.HTTPConnection("127.0.0.1", parsed.port,
                                              timeout=max(.001, deadline - time.monotonic()))
        else:
            from optimizer_ai import _PinnedHTTPSConnection, _resolve_public
            host = _CLOUD_HOSTS[provider][0]
            ip = _resolve_public(host)
            _check(cancel, deadline)
            conn = _PinnedHTTPSConnection(host, ip, max(.001, deadline - time.monotonic()))
        timer = threading.Timer(max(0, deadline - time.monotonic()), interrupt)
        timer.daemon = True
        timer.start()
        if cancel is not None:
            watcher = threading.Thread(target=watch_cancel, daemon=True,
                                       name="ai-backend-cancel")
            watcher.start()
        _check(cancel, deadline)
        # Retain the connected socket before request/getresponse can detach it.
        # A cancellation during connect is checked before any payload is sent.
        conn.connect()
        active_socket[0] = conn.sock
        _check(cancel, deadline)
        conn.request(method, path, body=data,
                     headers={"Content-Type": "application/json", **headers})
        _check(cancel, deadline)
        response = conn.getresponse()
        if not 200 <= response.status < 300:
            if response.status == 429:
                raise BackendError("AI service rate limit reached (HTTP 429).")
            raise BackendError(f"AI service request failed (HTTP {response.status}).")
        content = bytearray()
        while True:
            _check(cancel, deadline)
            chunk = response.read1(min(65536, _MAX_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > _MAX_BYTES:
                raise BackendError("AI response is too large.")
        _check(cancel, deadline)
        if response.length not in (None, 0):
            raise BackendError("AI response is truncated.")
        value = json.loads(content.decode("utf-8"))
        if not isinstance(value, dict) or "error" in value:
            raise BackendError("Invalid AI service response.")
        return value
    except BackendError:
        _check(cancel, deadline)
        raise
    except (ValueError, OSError, KeyError, TypeError, http.client.HTTPException):
        _check(cancel, deadline)
        raise BackendError("AI service connection or response failed.") from None
    finally:
        finished.set()
        if timer is not None:
            timer.cancel()
            timer.join()
        if watcher is not None:
            watcher.join()
        if response is not None:
            response.close()
        if conn is not None:
            conn.close()


def _remote(value) -> bool:
    if isinstance(value, dict):
        return any((key in ("remote_host", "remote_model") and bool(item))
                   or _remote(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_remote(item) for item in value)
    return False


def _inspect_ollama(model: str, cancel, deadline: float, metadata=None) -> None:
    if ":cloud" in model.lower() or "-cloud" in model.lower() or _remote(metadata):
        raise BackendError("Remote Ollama models are not allowed.")
    info = _request("ollama", "POST", "/api/show", {"model": model},
                    {}, cancel, deadline)
    if _remote(info):
        raise BackendError("Remote Ollama models are not allowed.")
    capabilities = info.get("capabilities")
    if not isinstance(capabilities, list) or "completion" not in capabilities:
        raise BackendError("Ollama model does not support completion.")


def _text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BackendError("AI response is empty or invalid.")
    if len(value.encode("utf-8")) > _MAX_BYTES:
        raise BackendError("AI response is too large.")
    return value.strip()


def _chatgpt_call(auth, operation, *args, **kwargs):
    """Translate known error types without exposing exception or response data."""
    try:
        return operation(*args, **kwargs)
    except ValueError as exc:
        messages = (
            ("CancelledError", "ChatGPT 요청이 취소되었습니다."),
            ("AuthenticationError", "ChatGPT 계정 연결을 확인하고 필요하면 다시 연결해 주세요."),
            ("UsageLimitError", "ChatGPT 사용이 현재 제한되어 있습니다. 잠시 후 다시 확인해 주세요."),
            ("NetworkError", "ChatGPT 연결에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 시도해 주세요."),
            ("ProtocolError", "ChatGPT 응답을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요."),
        )
        for name, message in messages:
            error_type = getattr(auth, name, None)
            if isinstance(error_type, type) and isinstance(exc, error_type):
                raise BackendError(message) from None
        raise


def generate(profile: AIProfile, instructions: str, payload: dict, *,
             cancel: threading.Event | None = None) -> str:
    """Send only the supplied instructions and JSON payload to this profile."""
    _validate(profile)
    deadline = time.monotonic() + _TIMEOUT
    _check(cancel, deadline)
    try:
        if not isinstance(instructions, str) or not instructions.strip() or not isinstance(payload, dict):
            raise BackendError("Invalid AI request.")
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len((instructions + text).encode("utf-8")) > _MAX_BYTES:
            raise BackendError("AI request is too large.")
        if profile.provider == "chatgpt-subscription":
            if not profile.account_id:
                raise BackendError("Connect a ChatGPT account first.")
            import chatgpt_auth
            result = _chatgpt_call(chatgpt_auth, chatgpt_auth.generate,
                                   profile.account_id, profile.model,
                                   instructions, payload, cancel=cancel)
            _check(cancel, deadline)
            return _text(result)
        messages = [{"role": "system", "content": instructions},
                    {"role": "user", "content": text}]
        if profile.provider == "ollama":
            _inspect_ollama(profile.model, cancel, deadline)
            value = _request("ollama", "POST", "/api/chat",
                             {"model": profile.model, "messages": messages, "stream": False},
                             {}, cancel, deadline)
            if value.get("done") is not True or value.get("done_reason") not in (None, "stop"):
                raise BackendError("AI response is incomplete.")
            return _text(value["message"]["content"])
        key = _secret(profile.provider)
        base = _CLOUD_HOSTS[profile.provider][1]
        if profile.provider == "gemini":
            value = _request("gemini", "POST",
                             base + "/models/" + quote(profile.model, safe="") + ":generateContent",
                             {"systemInstruction": {"parts": [{"text": instructions}]},
                              "contents": [{"role": "user", "parts": [{"text": text}]}]},
                             {"x-goog-api-key": key}, cancel, deadline)
            candidate = value["candidates"][0]
            if not isinstance(candidate, dict):
                raise BackendError("Invalid AI service response.")
            if candidate.get("finishReason") != "STOP":
                raise BackendError("AI response is incomplete or blocked.")
            parts = candidate["content"]["parts"]
            if not isinstance(parts, list) or not all(isinstance(part, dict) for part in parts):
                raise BackendError("Invalid AI service response.")
            return _text("".join(part["text"] for part in parts
                                 if not part.get("thought", False)))
        body = {"model": profile.model, "messages": messages, "stream": False}
        if profile.provider == "openrouter":
            # No model list, fallback, or paid plugin can enter through payload.
            if not profile.model.endswith(":free"):
                raise BackendError("OpenRouter requires an explicit :free model.")
            body["provider"] = {"allow_fallbacks": False}
            body["plugins"] = []
        value = _request(profile.provider, "POST", base + "/chat/completions",
                         body, {"Authorization": "Bearer " + key}, cancel, deadline)
        choice = value["choices"][0]
        if not isinstance(choice, dict):
            raise BackendError("Invalid AI service response.")
        if choice.get("finish_reason") != "stop":
            raise BackendError("AI response is incomplete or blocked.")
        return _text(choice["message"]["content"])
    except BackendError:
        raise
    except (ValueError, OSError, KeyError, IndexError, TypeError, http.client.HTTPException):
        _check(cancel, deadline)
        raise BackendError("AI generation failed.") from None


def list_models(profile: AIProfile, *,
                cancel: threading.Event | None = None) -> list[ModelInfo]:
    """Discover live model IDs; never silently substitute a selected model."""
    # Discovery has no selected model yet. This value is validation-only and
    # never sent as an inference request; generation still validates the model.
    discovery_profile = profile
    if profile.model == "":
        discovery_profile = replace(
            profile, model="discovery/model:free" if profile.provider == "openrouter" else "discovery")
    _validate(discovery_profile)
    deadline = time.monotonic() + _TIMEOUT
    _check(cancel, deadline)
    try:
        if profile.provider == "chatgpt-subscription":
            if not profile.account_id:
                raise BackendError("Connect a ChatGPT account first.")
            import chatgpt_auth
            records = _chatgpt_call(chatgpt_auth, chatgpt_auth.list_models,
                                    profile.account_id, cancel=cancel)
            result = []
            for record in records:
                if isinstance(record, ModelInfo):
                    result.append(ModelInfo(_text(record.id), _text(record.label)))
                else:
                    result.append(ModelInfo(_text(record["id"]), _text(record["label"])))
                if len(result) > _MAX_MODELS:
                    raise BackendError("Too many AI models.")
            _check(cancel, deadline)
            return result
        if profile.provider == "ollama":
            value = _request("ollama", "GET", "/api/tags", None, {}, cancel, deadline)
            records = value["models"]
        else:
            key = _secret(profile.provider)
            base = _CLOUD_HOSTS[profile.provider][1]
            headers = ({"x-goog-api-key": key} if profile.provider == "gemini"
                       else {"Authorization": "Bearer " + key})
            value = _request(profile.provider, "GET", base + "/models", None,
                             headers, cancel, deadline)
            records = value["models" if profile.provider == "gemini" else "data"]
        if not isinstance(records, list) or len(records) > _MAX_MODELS:
            raise BackendError("Invalid AI model list.")
        result = []
        for record in records:
            if not isinstance(record, dict):
                raise BackendError("Invalid AI model list.")
            if profile.provider == "ollama":
                model = _text(record.get("name", record.get("model")))
                if _remote(record) or ":cloud" in model.lower() or "-cloud" in model.lower():
                    continue
                try:
                    _inspect_ollama(model, cancel, deadline, record)
                except BackendError as exc:
                    if str(exc) in ("Remote Ollama models are not allowed.",
                                    "Ollama model does not support completion."):
                        continue
                    raise
                label = model
            elif profile.provider == "gemini":
                if "generateContent" not in record.get("supportedGenerationMethods", []):
                    continue
                name = _text(record["name"])
                if not name.startswith("models/"):
                    raise BackendError("Invalid Gemini model name.")
                model = name[len("models/"):]
                label = _text(record.get("displayName", model))
            else:
                model = _text(record["id"])
                if profile.provider == "openrouter" and not model.endswith(":free"):
                    continue
                label = _text(record.get("name", model))
            # Validate discovered IDs with the same restrictions as sending.
            validate_profile(replace(profile, model=model))
            result.append(ModelInfo(model, label))
        return result
    except BackendError:
        raise
    except (ValueError, OSError, KeyError, IndexError, TypeError, http.client.HTTPException):
        _check(cancel, deadline)
        raise BackendError("AI model discovery failed.") from None
