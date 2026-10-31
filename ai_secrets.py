"""Current-user Windows DPAPI storage. No plaintext or non-Windows fallback."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import tempfile


class SecretError(ValueError):
    """Sanitized credential-store failure."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class DPAPIProtector:
    def _crypt(self, data: bytes, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise SecretError("Windows credential protection is unavailable.")
        source = ctypes.create_string_buffer(data)
        blob = _Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
        result = _Blob()
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p,
                       ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                       wintypes.DWORD, ctypes.POINTER(_Blob)]
        fn.restype = wintypes.BOOL
        # CRYPTPROTECT_UI_FORBIDDEN; deliberately not LOCAL_MACHINE.
        if not fn(ctypes.byref(blob), None, None, None, None, 1,
                  ctypes.byref(result)):
            raise SecretError("Windows credential protection failed.")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            kernel.LocalFree(result.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._crypt(data, False)

    def unprotect(self, data: bytes) -> bytes:
        return self._crypt(data, True)


class SecretStore:
    """Inject root/protector only in tests; default always uses real DPAPI."""

    def __init__(self, root=None, protector=None):
        if root is None:
            local = os.environ.get("LOCALAPPDATA")
            if not local:
                raise SecretError("Windows local application storage is unavailable.")
            root = Path(local) / "SyeonPet" / "credentials"
        self.root = Path(root)
        self.protector = protector if protector is not None else DPAPIProtector()

    def _path(self, key):
        if not isinstance(key, str) or not key:
            raise SecretError("Invalid credential key.")
        return self.root / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".bin")

    def get_secret(self, key):
        try:
            data = self._path(key).read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise SecretError("Could not read protected credentials.") from None
        try:
            if len(data) > 4_000_000:
                raise ValueError()
            value = json.loads(self.protector.unprotect(data))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (OSError, ValueError, TypeError):
            raise SecretError("Could not open protected credentials.") from None

    def set_secret(self, key, value):
        path = self._path(key)
        if not isinstance(value, dict):
            raise SecretError("Credentials must be a JSON object.")
        name = None
        try:
            data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(data) > 2_000_000:
                raise ValueError()
            encrypted = self.protector.protect(data)
            self.root.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".pending-", dir=self.root)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            name = None
        except (OSError, ValueError, TypeError):
            raise SecretError("Could not save protected credentials.") from None
        finally:
            if name is not None:
                Path(name).unlink(missing_ok=True)

    def delete_secret(self, key):
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError:
            raise SecretError("Could not remove protected credentials.") from None


def get_secret(key):
    return SecretStore().get_secret(key)


def set_secret(key, value):
    SecretStore().set_secret(key, value)


def delete_secret(key):
    SecretStore().delete_secret(key)
