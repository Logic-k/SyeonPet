"""Prepare signed Windows releases without touching the running installation.

Network work is synchronous and must be called from a worker thread. A release
is usable only if its detached Ed25519 signature verifies with the trusted key
embedded in the installed app. The archive and exact metadata remain alongside
the staged files so they can be checked again immediately before launch.
"""
from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import queue
import re
import shutil
import stat
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit


API_URL = "https://api.github.com/repos/Logic-k/SyeonPet/releases/latest"
RELEASE_ROOT = "https://github.com/Logic-k/SyeonPet/releases/download/"
_API_HOST = "api.github.com"
_ASSET_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
_VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_HEX_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_MANIFEST = 65536
_MAX_ARCHIVE = 150 * 1024 * 1024
_MAX_EXPANDED = 600 * 1024 * 1024
_MAX_ENTRY = 100 * 1024 * 1024
_MAX_ENTRIES = 2048
_TIMEOUT = 20
_DOWNLOAD_DEADLINE = 300
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
_network_lock = threading.Lock()


class UpdateError(ValueError):
    """Safe-to-display update error; no request URL or user data is included."""


def _version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or len(value) > 32:
        raise UpdateError("업데이트 버전 형식을 확인할 수 없습니다.")
    match = _VERSION_RE.fullmatch(value)
    if not match:
        raise UpdateError("안정 버전만 자동 업데이트할 수 있습니다.")
    return tuple(int(part) for part in match.groups())


def _json_object(data: bytes, max_bytes: int) -> dict:
    if not isinstance(data, bytes) or len(data) > max_bytes:
        raise UpdateError("업데이트 정보의 크기가 올바르지 않습니다.")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise UpdateError("업데이트 정보를 읽을 수 없습니다.") from exc
    if not isinstance(value, dict):
        raise UpdateError("업데이트 정보 형식이 올바르지 않습니다.")
    return value


def verify_signature(public_key: bytes, message: bytes, signature: bytes) -> None:
    """Verify raw Ed25519 signature through the bundled OpenSSL 3 library."""
    if not isinstance(public_key, bytes) or len(public_key) != 32 or not isinstance(signature, bytes) or len(signature) != 64:
        raise UpdateError("업데이트 서명 또는 공개키 형식이 올바르지 않습니다.")
    if not isinstance(message, bytes) or len(message) > _MAX_MANIFEST:
        raise UpdateError("업데이트 서명 자료가 올바르지 않습니다.")
    library = Path(__file__).resolve().with_name("libcrypto-3.dll")
    try:
        crypto = ctypes.CDLL(str(library))
        crypto.EVP_PKEY_new_raw_public_key_ex.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                                         ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
        crypto.EVP_PKEY_new_raw_public_key_ex.restype = ctypes.c_void_p
        crypto.EVP_PKEY_free.argtypes = [ctypes.c_void_p]
        crypto.EVP_MD_CTX_new.restype = ctypes.c_void_p
        crypto.EVP_MD_CTX_free.argtypes = [ctypes.c_void_p]
        crypto.EVP_DigestVerifyInit.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        crypto.EVP_DigestVerifyInit.restype = ctypes.c_int
        crypto.EVP_DigestVerify.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                            ctypes.c_void_p, ctypes.c_size_t]
        crypto.EVP_DigestVerify.restype = ctypes.c_int
        key_data = ctypes.create_string_buffer(public_key)
        msg_data = ctypes.create_string_buffer(message)
        sig_data = ctypes.create_string_buffer(signature)
        key = crypto.EVP_PKEY_new_raw_public_key_ex(None, b"ED25519", None, key_data, len(public_key))
        if not key:
            raise UpdateError("업데이트 서명 검증을 시작할 수 없습니다.")
        try:
            ctx = crypto.EVP_MD_CTX_new()
            if not ctx:
                raise UpdateError("업데이트 서명 검증을 시작할 수 없습니다.")
            try:
                if (crypto.EVP_DigestVerifyInit(ctx, None, None, None, key) != 1 or
                        crypto.EVP_DigestVerify(ctx, sig_data, len(signature), msg_data, len(message)) != 1):
                    raise UpdateError("업데이트 서명이 올바르지 않습니다. 기존 버전을 계속 사용하세요.")
            finally:
                crypto.EVP_MD_CTX_free(ctx)
        finally:
            crypto.EVP_PKEY_free(key)
    except UpdateError:
        raise
    except (OSError, AttributeError, TypeError, ValueError) as exc:
        raise UpdateError("서명 검증 라이브러리를 사용할 수 없습니다. 기존 버전을 계속 사용하세요.") from exc


def parse_signed_manifest(raw: bytes, signature: bytes, public_key: bytes,
                          expected_version: str | None = None) -> dict:
    verify_signature(public_key, raw, signature)
    doc = _json_object(raw, _MAX_MANIFEST)
    if set(doc) != {"schema", "platform", "version", "asset", "size", "sha256"}:
        raise UpdateError("업데이트 서명 정보의 항목이 올바르지 않습니다.")
    version = doc["version"]
    _version(version)
    if (type(doc["schema"]) is not int or doc["schema"] != 1 or doc["platform"] != "windows-x64"
            or (expected_version is not None and version != expected_version)
            or doc["asset"] != f"SyeonPet-{version}-windows.zip"
            or type(doc["size"]) is not int or not 0 < doc["size"] <= _MAX_ARCHIVE
            or not isinstance(doc["sha256"], str) or not _HEX_RE.fullmatch(doc["sha256"])):
        raise UpdateError("서명된 업데이트 파일 정보가 올바르지 않습니다.")
    return doc


def _asset_names(version: str) -> tuple[str, str, str]:
    _version(version)
    prefix = f"SyeonPet-{version}"
    return prefix + "-windows.zip", prefix + "-update.json", prefix + "-update.sig"


def _safe_https(url: str, hosts: set[str], allow_query: bool = False) -> bool:
    try:
        value = urlsplit(url)
        return (value.scheme == "https" and value.hostname in hosts and value.port in (None, 443)
                and not value.username and not value.password and not value.fragment
                and (allow_query or not value.query))
    except (ValueError, TypeError):
        return False


class _AssetRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _safe_https(newurl, _ASSET_HOSTS, allow_query=True):
            raise UpdateError("업데이트 다운로드 주소를 확인할 수 없습니다.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UpdateError("업데이트 확인 주소가 변경되었습니다.")


def _download_body(url: str, limit: int) -> bytes:
    """Read one bounded response inside a dedicated daemon worker."""
    is_api = url == API_URL
    if not (_safe_https(url, {_API_HOST}) if is_api else _safe_https(url, {"github.com"})):
        raise UpdateError("업데이트 주소를 확인할 수 없습니다.")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                          _NoRedirect() if is_api else _AssetRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "SyeonPet-signed-updater",
                                                  "Accept": "application/vnd.github+json" if is_api else "application/octet-stream"})
    deadline = time.monotonic() + _DOWNLOAD_DEADLINE
    buf = bytearray()
    count = 0
    try:
        with opener.open(request, timeout=_TIMEOUT) as response:
            while True:
                if time.monotonic() > deadline:
                    raise UpdateError("업데이트 다운로드 시간이 초과되었습니다.")
                read = getattr(response, "read1", response.read)
                data = read(min(65536, limit + 1 - count))
                if not data:
                    break
                count += len(data)
                if count > limit:
                    raise UpdateError("업데이트 파일의 크기가 허용 범위를 벗어났습니다.")
                buf.extend(data)
    except UpdateError:
        raise
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise UpdateError("업데이트를 내려받지 못했습니다. 인터넷 연결을 확인해 주세요.") from exc
    return bytes(buf)


def _download(url: str, limit: int, output=None) -> bytes | None:
    """Bound DNS, redirects and the complete response, including slow peers.

    A stuck DNS or socket worker holds the process-wide lock until it actually
    exits. This prevents unbounded workers and never writes to a caller-owned
    stream after the caller has timed out or deleted its temporary directory.
    """
    if not _network_lock.acquire(blocking=False):
        raise UpdateError("업데이트 연결을 확인 중입니다. 잠시 후 다시 시도해 주세요.")
    result = queue.Queue(maxsize=1)

    def work():
        try:
            try:
                value = (_download_body(url, limit), None)
            except UpdateError as exc:
                value = (None, exc)
            except Exception:
                value = (None, UpdateError("업데이트를 내려받지 못했습니다. 인터넷 연결을 확인해 주세요."))
            result.put(value)
        finally:
            _network_lock.release()

    try:
        threading.Thread(target=work, daemon=True, name="syeon-update-download").start()
    except Exception as exc:
        _network_lock.release()
        raise UpdateError("업데이트 연결을 시작하지 못했습니다.") from exc
    try:
        data, error = result.get(timeout=_DOWNLOAD_DEADLINE)
    except queue.Empty as exc:
        raise UpdateError("업데이트 다운로드 시간이 초과되었습니다.") from exc
    if error is not None:
        raise error
    if output is not None:
        output.write(data)
        return None
    return data


def _checked_release(raw: bytes, current_version: str) -> tuple[str | None, dict]:
    current = _version(current_version)
    doc = _json_object(raw, _MAX_MANIFEST)
    tag = doc.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise UpdateError("공식 안정 버전 정보를 확인할 수 없습니다.")
    version = tag[1:]
    parsed = _version(version)
    if (doc.get("draft") is not False or doc.get("prerelease") is not False or
            doc.get("html_url") != "https://github.com/Logic-k/SyeonPet/releases/tag/" + quote(tag, safe="")):
        raise UpdateError("공식 안정 릴리스 정보를 확인할 수 없습니다.")
    if parsed <= current:
        return None, {}
    expected = _asset_names(version)
    assets = doc.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("서명된 업데이트 파일이 없습니다.")
    found = {}
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") in expected:
            name = asset["name"]
            url = RELEASE_ROOT + tag + "/" + name
            if name in found or asset.get("state") != "uploaded" or asset.get("browser_download_url") != url:
                raise UpdateError("공식 업데이트 파일 정보를 확인할 수 없습니다.")
            found[name] = url
    if set(found) != set(expected):
        raise UpdateError("이 버전에는 서명된 자동 업데이트 파일이 없습니다. 기존 버전을 계속 사용하세요.")
    return version, found


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 512 or "\\" in name or ":" in name or name.startswith("/"):
        raise UpdateError("업데이트 압축 파일에 잘못된 경로가 있습니다.")
    parts = name.split("/")
    if any(not part or part in (".", "..") or part.endswith((".", " ")) or
           any(ord(char) < 32 or char in '<>"|?*' for char in part) or
           unicodedata.normalize("NFKC", part.split(".")[0]).upper() in _RESERVED
           for part in parts):
        raise UpdateError("업데이트 압축 파일에 사용할 수 없는 경로가 있습니다.")
    return str(PurePosixPath(*parts))


def _archive_entries(archive: zipfile.ZipFile) -> dict:
    infos = archive.infolist()
    if not infos or len(infos) > _MAX_ENTRIES:
        raise UpdateError("업데이트 파일 개수가 올바르지 않습니다.")
    names = {}
    folded = set()
    total = 0
    for info in infos:
        name = _safe_name(info.filename)
        kind = stat.S_IFMT(info.external_attr >> 16)
        folded_name = unicodedata.normalize("NFKC", name).casefold()
        if (info.is_dir() or kind not in (0, stat.S_IFREG) or info.flag_bits & 1
                or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                or info.file_size > _MAX_ENTRY or info.file_size < 0 or folded_name in folded):
            raise UpdateError("업데이트 압축 파일 항목이 올바르지 않습니다.")
        total += info.file_size
        if total > _MAX_EXPANDED:
            raise UpdateError("업데이트 압축을 풀 공간이 너무 큽니다.")
        folded.add(folded_name)
        names[name] = info
    for name in names:
        parts = name.split("/")
        if any(unicodedata.normalize("NFKC", "/".join(parts[:i])).casefold() in folded
               for i in range(1, len(parts))):
            raise UpdateError("업데이트 파일 경로가 충돌합니다.")
    if not {"package-manifest.json", "SyeonPet.exe", "pythonw.exe", "main.py", "config.json", "version.py"} <= names.keys():
        raise UpdateError("업데이트에 필요한 파일이 없습니다.")
    return names


def _hash_file(path: Path, limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(65536), b""):
            size += len(chunk)
            if size > limit:
                raise UpdateError("업데이트 파일이 너무 큽니다.")
            digest.update(chunk)
    return size, digest.hexdigest()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(os.path, "isjunction", lambda _: False)(path))


def _verify_archive(archive_path: Path, doc: dict) -> dict:
    if _is_link(archive_path) or not archive_path.is_file():
        raise UpdateError("준비된 업데이트 파일을 찾을 수 없습니다.")
    size, digest = _hash_file(archive_path, _MAX_ARCHIVE)
    if size != doc["size"] or not hmac.compare_digest(digest, doc["sha256"]):
        raise UpdateError("업데이트 파일의 크기 또는 무결성이 일치하지 않습니다.")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = _archive_entries(archive)
            info = names["package-manifest.json"]
            if info.file_size > _MAX_MANIFEST:
                raise UpdateError("패키지 정보가 너무 큽니다.")
            manifest = _json_object(archive.read(info), _MAX_MANIFEST)
            files = manifest.get("files")
            if (manifest.get("version") != doc["version"] or not isinstance(files, dict)
                    or set(files) != set(names) - {"package-manifest.json"}):
                raise UpdateError("패키지의 버전 또는 파일 목록이 일치하지 않습니다.")
            for name, expected in files.items():
                if not isinstance(expected, str) or not _HEX_RE.fullmatch(expected):
                    raise UpdateError("패키지 파일 해시가 올바르지 않습니다.")
                digest = hashlib.sha256()
                with archive.open(names[name]) as file:
                    for chunk in iter(lambda: file.read(65536), b""):
                        digest.update(chunk)
                if not hmac.compare_digest(digest.hexdigest(), expected):
                    raise UpdateError("패키지 파일의 무결성이 일치하지 않습니다.")
            return {name: (hashlib.sha256(archive.read(names[name])).hexdigest()
                           if name == "package-manifest.json" else files[name]) for name in names}
    except UpdateError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        raise UpdateError("업데이트 압축 파일을 읽지 못했습니다.") from exc


def verify_staged(version_dir: Path | str, public_key: bytes, current_version: str | None = None) -> dict:
    """Recheck a staged release, including every extracted file, before launch."""
    root = Path(version_dir)
    if _is_link(root) or not root.is_dir():
        raise UpdateError("준비된 업데이트 폴더를 확인할 수 없습니다.")
    _version(root.name)
    paths = [root / "manifest.json", root / "manifest.sig", root / "archive.zip", root / "app"]
    if any(_is_link(p) for p in paths):
        raise UpdateError("업데이트 경로에 링크를 사용할 수 없습니다.")
    try:
        if paths[0].stat().st_size > _MAX_MANIFEST or paths[1].stat().st_size != 64:
            raise UpdateError("업데이트 서명 정보의 크기가 올바르지 않습니다.")
        doc = parse_signed_manifest(paths[0].read_bytes(), paths[1].read_bytes(), public_key, root.name)
        if current_version is not None and _version(doc["version"]) <= _version(current_version):
            raise UpdateError("현재보다 오래된 버전은 자동 적용할 수 없습니다.")
        expected = _verify_archive(paths[2], doc)
        app = paths[3]
        if not app.is_dir():
            raise UpdateError("준비된 앱 폴더가 없습니다.")
        actual = set()
        for directory, subdirs, files in os.walk(app, followlinks=False):
            base = Path(directory)
            for name in subdirs + files:
                if _is_link(base / name):
                    raise UpdateError("준비된 앱에 링크를 사용할 수 없습니다.")
            for name in files:
                path = base / name
                rel = path.relative_to(app).as_posix()
                _safe_name(rel)
                if rel not in expected:
                    raise UpdateError("준비된 앱에 예상하지 못한 파일이 있습니다.")
                _, digest = _hash_file(path, _MAX_ENTRY)
                if not hmac.compare_digest(digest, expected[rel]):
                    raise UpdateError("준비된 앱의 파일이 변경되었습니다.")
                actual.add(rel)
        if actual != set(expected):
            raise UpdateError("준비된 앱의 일부 파일이 없습니다.")
        return doc
    except UpdateError:
        raise
    except (OSError, ValueError) as exc:
        raise UpdateError("준비된 업데이트를 확인하지 못했습니다.") from exc


def prepare_update(current_version: str, state_dir: Path | str, public_key: bytes) -> dict:
    """Fetch and stage a signed release. Call only from a background worker."""
    try:
        return _prepare_update(current_version, state_dir, public_key)
    except OSError as exc:
        # Includes Windows MAX_PATH, permission and storage failures. Do not
        # expose local file names, and retain the currently installed app.
        raise UpdateError("업데이트 파일을 준비하지 못했습니다. 저장 공간과 폴더 경로를 확인해 주세요. 기존 버전은 유지됩니다.") from exc


def _prepare_update(current_version: str, state_dir: Path | str, public_key: bytes) -> dict:
    version, assets = _checked_release(_download(API_URL, _MAX_MANIFEST), current_version)
    if version is None:
        return {"status": "current", "version": current_version, "staged_path": ""}
    zip_name, manifest_name, signature_name = _asset_names(version)
    raw = _download(assets[manifest_name], _MAX_MANIFEST)
    signature = _download(assets[signature_name], 64)
    doc = parse_signed_manifest(raw, signature, public_key, version)
    base = Path(state_dir)
    for parent in (base, *base.parents):
        if parent.exists() and _is_link(parent):
            raise UpdateError("업데이트 저장 경로에 링크를 사용할 수 없습니다.")
    versions = base / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    if _is_link(versions):
        raise UpdateError("업데이트 저장 경로에 링크를 사용할 수 없습니다.")
    final = versions / version
    if os.path.lexists(final):
        verify_staged(final, public_key, current_version)
        return {"status": "already_ready", "version": version, "staged_path": str(final / "app")}
    # Keep the transient extraction path short. Qt distributions contain
    # deeply nested license files and the embedded Windows launcher may not be
    # configured for paths exceeding MAX_PATH. The temporary directory is on
    # the same volume as the final location, so the final rename remains atomic.
    temporary = Path(tempfile.mkdtemp(prefix=".p-", dir=base))
    try:
        staged = temporary / version
        staged.mkdir()
        (staged / "manifest.json").write_bytes(raw)
        (staged / "manifest.sig").write_bytes(signature)
        archive_path = staged / "archive.zip"
        with archive_path.open("xb") as output:
            _download(assets[zip_name], doc["size"], output)
        _verify_archive(archive_path, doc)
        app = staged / "app"
        app.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            for name, info in _archive_entries(archive).items():
                target = app.joinpath(*name.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, target.open("xb") as dest:
                    shutil.copyfileobj(src, dest, length=65536)
        # Use the intended version folder name for the same full verification
        # without trusting an unverified final location.
        verify_staged(staged, public_key, current_version)
        try:
            staged.rename(final)  # fails rather than replacing an existing directory
        except OSError as exc:
            raise UpdateError("업데이트 준비 중 다른 작업이 진행되었습니다. 다시 확인해 주세요.") from exc
        return {"status": "ready", "version": version, "staged_path": str(final / "app")}
    finally:
        # This directory was newly created by this invocation only. Never touch
        # another version, configuration, library or pre-existing user file.
        shutil.rmtree(temporary, ignore_errors=True)
