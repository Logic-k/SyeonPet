"""Launch verified update versions without replacing a running installation.

The initial installation remains the stable launcher and owns ``config.json``.
Only version identifiers are persisted; candidate paths are derived locally.
The caller supplies the signature verifier and the pinned public key.
"""
from __future__ import annotations

import contextlib
import atexit
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
from pathlib import Path


class SwitchError(ValueError):
    """An update state or candidate could not be safely used."""


_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
_ROOT_ENV = "SYEONPET_INSTALL_ROOT"
_CHILD_ENV = "SYEONPET_UPDATE_CHILD"
_TOKEN_ENV = "SYEONPET_UPDATE_READY_TOKEN"
_instance_lock = None
_HEALTH_NOTICE_SECONDS = 30


def _version(value):
    if not isinstance(value, str) or len(value) > 32 or not _VERSION.fullmatch(value):
        raise SwitchError("잘못된 업데이트 버전입니다.")
    return value


def _version_tuple(value):
    return tuple(map(int, _version(value).split(".")))


def _install_root(value):
    root = Path(value)
    if not root.is_absolute():
        raise SwitchError("설치 경로가 절대 경로가 아닙니다.")
    return root.resolve()


def state_directory(install_root):
    """Per-installation local state, distinct even for two portable installs."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise SwitchError("사용자 업데이트 저장 위치를 확인할 수 없습니다.")
    root = _install_root(install_root)
    identity = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()
    updates = Path(base) / "SyeonPet" / "updates"
    # Retain any previously created state exactly where it was. New installs
    # use 128 bits of the hash: this still identifies portable installs with a
    # negligible collision probability and leaves room for bundled Qt paths on
    # Windows systems without long-path support.
    old = updates / identity
    if os.path.lexists(old):
        return old
    return updates / identity[:32]


def _no_links(base, path):
    """Reject links/reparse points in all candidate components."""
    base, path = Path(base), Path(path)
    try:
        parts = path.relative_to(base).parts
    except ValueError as exc:
        raise SwitchError("업데이트 경로가 저장 위치 밖입니다.") from exc
    current = base
    for part in ("", *parts):
        if part:
            current = current / part
        if current.is_symlink():
            raise SwitchError("업데이트 경로에 링크를 사용할 수 없습니다.")
        if os.name == "nt" and current.exists():
            attrs = getattr(current.stat(follow_symlinks=False), "st_file_attributes", 0)
            if attrs & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT, including junctions
                raise SwitchError("업데이트 경로에 재분석 지점을 사용할 수 없습니다.")
    if not path.resolve().is_relative_to(base.resolve()):
        raise SwitchError("업데이트 경로가 저장 위치 밖입니다.")


def _version_dir(root, version):
    directory = state_directory(root)
    candidate = directory / "versions" / _version(version)
    _no_links(directory, candidate)
    return candidate


class _FileLock:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def acquire(self, timeout=0):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise SwitchError("잠금 파일에 링크를 사용할 수 없습니다.")
        handle = open(self.path, "a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        end = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.file = handle
                return True
            except OSError:
                if time.monotonic() >= end:
                    handle.close()
                    return False
                time.sleep(.05)

    def release(self):
        if self.file is None:
            return
        try:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.file = None

    def describe(self, version, trial=False):
        """Write non-sensitive process metadata beyond the locked first byte."""
        self.file.seek(1)
        self.file.truncate()
        self.file.write(json.dumps({"version": version, "trial": trial}).encode("ascii"))
        self.file.flush()


def _running_instances(directory):
    """Read only locks held by live processes; stale files are harmless."""
    result = []
    for path in Path(directory).glob("instance-*.lock"):
        if not re.fullmatch(r"instance-[0-9a-f]{32}\.lock", path.name):
            continue
        probe = _FileLock(path)
        if probe.acquire():
            # A supervisor may have crashed after starting its candidate but
            # before the candidate registered its own lock. A stale trial is
            # therefore not proof that no candidate is still starting.
            try:
                probe.file.seek(1)
                raw = probe.file.read(1025)
                item = json.loads(raw.decode("ascii"))
                if item.get("trial") is True:
                    result.append({"version": None, "trial": True})
            except (OSError, UnicodeError, ValueError, AttributeError):
                result.append({"version": None, "trial": True})
            finally:
                probe.release()
            continue
        try:
            with path.open("rb") as handle:
                handle.seek(1)
                data = handle.read(1025)
            if len(data) > 1024:
                raise ValueError("instance information too large")
            item = json.loads(data.decode("ascii"))
            version = item.get("version")
            if version is not None:
                _version(version)
            if type(item.get("trial")) is not bool:
                raise ValueError("invalid instance information")
            result.append({"version": version, "trial": item["trial"]})
        except (OSError, UnicodeError, ValueError, AttributeError):
            # Unknown active state is treated as an in-progress trial so that
            # it can never cause an unsafe version transition.
            result.append({"version": None, "trial": True})
    return result


def _register_instance(directory, state):
    """Choose a compatible version and hold an individual process lock."""
    global _instance_lock
    running = _running_instances(directory)
    if any(item["trial"] for item in running):
        return None, False
    if running:
        versions = {item["version"] for item in running}
        if len(versions) != 1:
            return None, False
        version = versions.pop()
        # Never start a different release while another instance is active.
        if version is not None and state["active"] != version:
            return None, False
        trial = False
    else:
        version = state["pending"] or state["active"] or state["previous"]
        trial = version is not None and version == state["pending"]
    lock = _FileLock(Path(directory) / ("instance-" + secrets.token_hex(16) + ".lock"))
    if not lock.acquire():
        raise SwitchError("실행 상태를 기록하지 못했습니다.")
    lock.describe(version, trial)
    _instance_lock = lock
    atexit.register(lock.release)
    return version, True


@contextlib.contextmanager
def _state_lock(directory):
    lock = _FileLock(Path(directory) / "state.lock")
    if not lock.acquire(timeout=3):
        raise SwitchError("업데이트 상태가 사용 중입니다.")
    try:
        yield
    finally:
        lock.release()


def _read_state(directory):
    path = Path(directory) / "state.json"
    if path.is_symlink():
        raise SwitchError("업데이트 상태에 링크를 사용할 수 없습니다.")
    try:
        if path.stat().st_size > 8192:
            raise SwitchError("업데이트 상태가 너무 큽니다.")
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": 1, "active": None, "previous": None, "pending": None}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SwitchError("업데이트 상태를 읽지 못했습니다.") from exc
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise SwitchError("업데이트 상태 형식이 올바르지 않습니다.")
    result = {"schema": 1}
    for name in ("active", "previous", "pending"):
        item = value.get(name)
        result[name] = _version(item) if item is not None else None
    return result


def _write_state(directory, value):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "state.json"
    if target.is_symlink():
        raise SwitchError("업데이트 상태에 링크를 사용할 수 없습니다.")
    fd, name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def queue_update(install_root, version, current_version=None):
    """Record a fully prepared candidate; never replace an existing pending one."""
    version = _version(version)
    directory = state_directory(install_root)
    candidate = _version_dir(install_root, version)
    if not (candidate / "app" / "SyeonPet.exe").is_file():
        raise SwitchError("준비된 업데이트 실행 파일이 없습니다.")
    _no_links(directory, candidate / "app" / "SyeonPet.exe")
    with _state_lock(directory):
        state = _read_state(directory)
        minimums = [item for item in (state["active"], current_version) if item is not None]
        if state["pending"] or any(_version_tuple(version) <= _version_tuple(item) for item in minimums):
            return False
        state["pending"] = version
        _write_state(directory, state)
    return True


def pending_version(install_root):
    """Return the prepared version without exposing or modifying state files."""
    directory = state_directory(install_root)
    with _state_lock(directory):
        return _read_state(directory)["pending"]


def _child_details(app_root):
    if os.environ.get(_CHILD_ENV) is None:
        return None
    version = _version(os.environ.get(_CHILD_ENV))
    root = _install_root(os.environ.get(_ROOT_ENV, ""))
    token = os.environ.get(_TOKEN_ENV, "")
    if not _TOKEN.fullmatch(token):
        raise SwitchError("업데이트 실행 확인 정보가 올바르지 않습니다.")
    expected = _version_dir(root, version) / "app"
    _no_links(state_directory(root), expected)
    if _install_root(app_root) != expected.resolve():
        raise SwitchError("업데이트 실행 경로가 올바르지 않습니다.")
    return root, version, token


def installation_root(app_root):
    """Return the original config/launcher directory for a verified child."""
    child = _child_details(app_root)
    return child[0] if child else _install_root(app_root)


def notify_ready():
    """Child calls after application initialization; no user content is written."""
    version = os.environ.get(_CHILD_ENV)
    if version is None:
        return
    root = _install_root(os.environ.get(_ROOT_ENV, ""))
    token = os.environ.get(_TOKEN_ENV, "")
    if not _TOKEN.fullmatch(token):
        raise SwitchError("업데이트 실행 확인 정보가 올바르지 않습니다.")
    directory = state_directory(root)
    marker_dir = directory / "ready"
    marker_dir.mkdir(parents=True, exist_ok=True)
    _no_links(directory, marker_dir)
    marker = marker_dir / (token + ".json")
    with marker.open("x", encoding="utf-8") as stream:
        json.dump({"version": _version(version), "token": token}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    # A candidate retains its own lock even if its launcher/supervisor exits.
    # Registration and metadata changes are serialized with other launches.
    if _instance_lock is not None:
        with _state_lock(directory):
            _instance_lock.describe(_version(version), False)


def _verified(root, version, public_key, verifier):
    candidate = _version_dir(root, version)
    info = verifier(candidate, public_key)
    if not isinstance(info, dict) or info.get("version") != version:
        raise SwitchError("업데이트 버전 검증에 실패했습니다.")
    app = candidate / "app"
    _no_links(state_directory(root), app / "SyeonPet.exe")
    if not (app / "SyeonPet.exe").is_file():
        raise SwitchError("업데이트 실행 파일이 없습니다.")
    return app


def _set_state(directory, mutate):
    with _state_lock(directory):
        state = _read_state(directory)
        mutate(state)
        _write_state(directory, state)


def _health_delay_notice():
    """Tell the user without blocking supervision or killing a live app."""
    if os.name == "nt":
        def show():
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, "션의 시작 확인이 지연되고 있습니다. 열려 있는 창의 내용을 저장한 뒤 "
                      "션을 직접 종료하고 다시 실행해 주세요.", "션 업데이트", 0x40)
        threading.Thread(target=show, daemon=True).start()


def _run_child(root, version, app):
    directory = state_directory(root)
    token = secrets.token_hex(16)
    marker_dir = directory / "ready"
    marker_dir.mkdir(parents=True, exist_ok=True)
    _no_links(directory, marker_dir)
    marker = marker_dir / (token + ".json")
    env = os.environ.copy()
    env.update({_ROOT_ENV: str(root), _CHILD_ENV: version, _TOKEN_ENV: token})
    # The signed candidate directory is immutable; bytecode caches would make
    # the next full verification fail and must not be written into it.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen([str(app / "SyeonPet.exe")], cwd=str(app), env=env,
                               creationflags=flags)
    ready = False
    notice = False
    started = time.monotonic()
    try:
        while process.poll() is None:
            if not ready and marker.is_file() and not marker.is_symlink():
                try:
                    item = json.loads(marker.read_text(encoding="utf-8"))
                    ready = item == {"version": version, "token": token}
                except (OSError, UnicodeError, json.JSONDecodeError):
                    pass
                if ready:
                    def activate(state):
                        if state["pending"] == version:
                            state["previous"] = state["active"]
                            state["active"] = version
                            state["pending"] = None
                    try:
                        with _state_lock(directory):
                            state = _read_state(directory)
                            activate(state)
                            _write_state(directory, state)
                            _instance_lock.describe(version, False)
                    except (OSError, SwitchError):
                        # The child is already live and may contain user work.
                        # Keep supervising it rather than starting a second app.
                        pass
            if not ready and not notice and time.monotonic() - started >= _HEALTH_NOTICE_SECONDS:
                _health_delay_notice()
                notice = True
            time.sleep(.1)
        return process.returncode, ready
    except Exception:
        # Popen succeeded, so a live child may be holding user work. A failed
        # poll/read/lock is not proof of termination. Never launch a fallback
        # until the OS wait call positively confirms that the child exited.
        try:
            _health_delay_notice()
        except Exception:
            pass
        while True:
            try:
                return process.wait(), ready
            except Exception:
                time.sleep(1)


def bootstrap(app_root, public_key, verify_staged, base_version=None):
    """Return None to run root/child, or an exit code after supervising a child.

    Never terminate a live candidate on a health timeout: it might already hold
    an unsaved user draft. If it exits abnormally, a verified previous version
    or the unchanged root can start under the same installation lock.
    """
    global _instance_lock
    child = _child_details(app_root)
    if child:
        root, version, _ = child
        _verified(root, version, public_key, verify_staged)
        directory = state_directory(root)
        with _state_lock(directory):
            lock = _FileLock(directory / ("instance-" + secrets.token_hex(16) + ".lock"))
            if not lock.acquire():
                raise SwitchError("실행 상태를 기록하지 못했습니다.")
            lock.describe(version, True)
            _instance_lock = lock
            atexit.register(lock.release)
        return None
    root = _install_root(app_root)
    directory = state_directory(root)
    directory.mkdir(parents=True, exist_ok=True)
    _no_links(directory, directory)
    with _state_lock(directory):
        state = _read_state(directory)
        if base_version is not None:
            base = _version_tuple(base_version)
            changed = False
            for name in ("active", "previous"):
                if state[name] and _version_tuple(state[name]) < base:
                    state[name] = None
                    changed = True
            floor = max(base, _version_tuple(state["active"]) if state["active"] else base)
            if state["pending"] and _version_tuple(state["pending"]) <= floor:
                state["pending"] = None
                changed = True
            if changed:
                _write_state(directory, state)
        version, allowed = _register_instance(directory, state)
    if not allowed:
        # Do not start incompatible copies during the short activation window.
        # The currently running instance is never terminated.
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "션이 업데이트를 확인 중입니다. 잠시 후 다시 실행해 주세요. "
                                             "계속 표시되면 열려 있는 내용을 저장하고 모든 션을 종료한 뒤 지원을 요청해 주세요.",
                                             "션 업데이트", 0x40)
        return 0
    tried = set()
    while True:
        if version is None:
            return None
        tried.add(version)
        try:
            app = _verified(root, version, public_key, verify_staged)
            code, ready = _run_child(root, version, app)
        except (OSError, ValueError, SwitchError):
            code, ready = 1, False
        if ready and code == 0:
            with _state_lock(directory):
                _instance_lock.describe(version, False)
            return 0
        with _state_lock(directory):
            # Our own lock is included; count all holders to avoid rolling
            # back while another process can still be editing with this app.
            live_count = len(_running_instances(directory))
            if live_count > 1:
                _instance_lock.describe(version, False)
                return code or 1
            state = _read_state(directory)
            if state["pending"] == version:
                state["pending"] = None
            if state["active"] == version:
                state["active"] = state["previous"] if state["previous"] != version else None
                state["previous"] = None
            _write_state(directory, state)
            os.environ["SYEONPET_UPDATE_ROLLBACK"] = "1"
            version = next((value for value in (state["pending"], state["active"], state["previous"])
                            if value and value not in tried), None)
            _instance_lock.describe(version, version == state["pending"] and version is not None)
