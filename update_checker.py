"""Check the official GitHub release; never download or install updates.

The fixed endpoint receives the user's IP address. It is queried once at
startup and when the user chooses the update menu; no prompt or settings are
sent. The caller chooses whether to show an error for a manual check.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import urllib.error
import urllib.request
from urllib.parse import quote

from PySide6.QtCore import QObject, Signal

from version import APP_VERSION


API_URL = "https://api.github.com/repos/Logic-k/SyeonPet/releases/latest"
RELEASE_BASE = "https://github.com/Logic-k/SyeonPet/releases/tag/"
_MAX_RESPONSE = 128 * 1024
_TIMEOUT_SECONDS = 8
_VERSION_RE = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_network_lock = threading.Lock()


def parse_version(value: object) -> tuple[int, int, int]:
    """Only stable major.minor.patch tags are accepted, never prereleases."""
    if not isinstance(value, str) or len(value) > 32:
        raise ValueError("지원하지 않는 버전 형식입니다.")
    match = _VERSION_RE.fullmatch(value)
    if not match:
        raise ValueError("지원하지 않는 버전 형식입니다.")
    return tuple(int(part) for part in match.groups())


def _parse_release(data: object, installed: str = APP_VERSION) -> dict:
    if not isinstance(data, dict) or data.get("draft") is not False or data.get("prerelease") is not False:
        raise ValueError("안정 릴리스 정보를 확인할 수 없습니다.")
    tag = data.get("tag_name")
    latest = parse_version(tag)
    current = parse_version(installed)
    # Never trust a URL supplied by a release body or API response. Both the
    # repository and the validated stable version are fixed/validated here.
    url = RELEASE_BASE + quote(tag, safe="")
    if data.get("html_url") != url:
        raise ValueError("공식 릴리스 주소를 확인할 수 없습니다.")
    if latest > current:
        return {"status": "available", "version": tag, "release_url": url,
                "message": f"새 버전 {tag}이 있습니다. 릴리스 페이지에서 확인해 주세요."}
    return {"status": "current", "version": tag, "release_url": url,
            "message": "현재 버전이 최신 안정 릴리스와 같거나 더 새롭습니다."}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _lookup_latest() -> dict:
    """Perform the fixed lookup; check_latest bounds this call as a whole."""
    request = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "SyeonPet-update-check",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read(_MAX_RESPONSE + 1)
        if len(raw) > _MAX_RESPONSE:
            raise ValueError("업데이트 응답이 너무 큽니다.")
        return _parse_release(json.loads(raw.decode("utf-8")))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"status": "no_release", "version": "", "release_url": "",
                    "message": "공식 안정 릴리스가 아직 없습니다."}
        return {"status": "unavailable", "version": "", "release_url": "",
                "message": f"업데이트를 확인하지 못했습니다 (HTTP {exc.code})."}
    except (urllib.error.URLError, OSError, TimeoutError, ValueError, UnicodeError, RecursionError):
        return {"status": "unavailable", "version": "", "release_url": "",
                "message": "업데이트를 확인하지 못했습니다. 인터넷 연결을 확인하고 다시 시도해 주세요."}


def check_latest() -> dict:
    """Bound the complete DNS, connection and read without blocking the UI.

    urllib's timeout applies per socket operation, not to an entire response.
    A single daemon network worker prevents slow bytes or DNS from exceeding
    the caller's deadline. At most one lingering worker can exist, even if a
    resolver or remote server stalls; later attempts report unavailable.
    """
    unavailable = {"status": "unavailable", "version": "", "release_url": "",
                   "message": "업데이트를 확인하지 못했습니다. 인터넷 연결을 확인하고 다시 시도해 주세요."}
    if not _network_lock.acquire(blocking=False):
        return unavailable
    result = queue.Queue(maxsize=1)

    def lookup():
        try:
            value = _lookup_latest()
        except Exception:
            value = unavailable
        finally:
            _network_lock.release()
        result.put(value)

    try:
        threading.Thread(target=lookup, daemon=True, name="syeon-update-network").start()
    except Exception:
        _network_lock.release()
        return unavailable
    try:
        return result.get(timeout=_TIMEOUT_SECONDS)
    except queue.Empty:
        return unavailable


class UpdateChecker(QObject):
    """Async check with one worker; a manual request joins an active check.

    `completed` emits a dict with status, version, release_url, message and
    manual. Startup failures can therefore remain quiet without hiding a
    user's later explicit check. No QApplication is created here.
    """
    completed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._running = False
        self._manual_requested = False

    def check(self, manual=False):
        with self._lock:
            self._manual_requested = self._manual_requested or bool(manual)
            if self._running:
                return
            self._running = True

        def work():
            try:
                result = check_latest()
            except Exception:  # ensure a failed worker never blocks future checks
                result = {"status": "unavailable", "version": "", "release_url": "",
                          "message": "업데이트를 확인하지 못했습니다. 다시 시도해 주세요."}
            with self._lock:
                result["manual"] = self._manual_requested
                self._manual_requested = False
                self._running = False
            try:
                self.completed.emit(result)
            except RuntimeError:
                # App or QObject was closed during the network request.
                pass

        threading.Thread(target=work, daemon=True, name="syeon-update-check").start()
