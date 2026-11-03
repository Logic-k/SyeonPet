"""Sign a verified portable release for the built-in Windows updater.

The Ed25519 private key is supplied by path and must remain outside the
repository and release artifacts. Signing uses the bundled OpenSSL 3
libcrypto DLL shipped with the app; the openssl CLI is only a fallback.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # The portable app contains CPython 3.12 extension DLLs in its root. Keep
    # the build interpreter's standard library ahead of that directory.
    sys.path.append(str(ROOT))

from update_trust import PUBLIC_KEY_BYTES
import updater_core

_NAME = re.compile(r"SyeonPet-((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))-windows\.zip\Z")
_ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


def _openssl(argv: list[str]) -> bytes:
    try:
        # Do not include OpenSSL stderr in error output: it can include local
        # key paths or information the operator does not intend to publish.
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                check=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("OpenSSL로 서명 키를 읽거나 서명을 생성하지 못했습니다.") from exc
    return result.stdout


def _pem_private_key_der(private_key: Path) -> bytes:
    """Decode an unencrypted PKCS#8 PEM ('BEGIN PRIVATE KEY') into DER bytes."""
    try:
        data = private_key.read_bytes()
    except OSError as exc:
        raise ValueError("서명 키를 읽거나 서명을 생성하지 못했습니다.") from exc
    if b"-----BEGIN ENCRYPTED PRIVATE KEY-----" in data:
        raise ValueError("암호화된 개인키는 지원하지 않습니다. 암호를 해제한 PEM을 사용하세요.")
    begin, end = b"-----BEGIN PRIVATE KEY-----", b"-----END PRIVATE KEY-----"
    if begin not in data or end not in data:
        raise ValueError("PKCS#8 PEM 개인키(BEGIN PRIVATE KEY)가 필요합니다.")
    body = data.split(begin, 1)[1].split(end, 1)[0]
    try:
        return base64.b64decode(re.sub(rb"\s", b"", body), validate=True)
    except ValueError as exc:
        raise ValueError("PEM 개인키 디코딩에 실패했습니다.") from exc


class _Libcrypto:
    """Thin ctypes wrapper over the bundled libcrypto-3.dll for Ed25519."""

    def __init__(self) -> None:
        library = Path(updater_core.__file__).resolve().with_name("libcrypto-3.dll")
        self.crypto = ctypes.CDLL(str(library))
        c = self.crypto
        c.d2i_AutoPrivateKey.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)), ctypes.c_long]
        c.d2i_AutoPrivateKey.restype = ctypes.c_void_p
        c.EVP_PKEY_free.argtypes = [ctypes.c_void_p]
        c.EVP_PKEY_get_raw_public_key.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        c.EVP_PKEY_get_raw_public_key.restype = ctypes.c_int
        c.EVP_MD_CTX_new.restype = ctypes.c_void_p
        c.EVP_MD_CTX_free.argtypes = [ctypes.c_void_p]
        c.EVP_DigestSignInit.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p]
        c.EVP_DigestSignInit.restype = ctypes.c_int
        c.EVP_DigestSign.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                                   ctypes.c_void_p, ctypes.c_size_t]
        c.EVP_DigestSign.restype = ctypes.c_int

    def load_private_key(self, private_key: Path) -> ctypes.c_void_p:
        der = _pem_private_key_der(private_key)
        buf = ctypes.create_string_buffer(der)
        pp = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        pkey = self.crypto.d2i_AutoPrivateKey(None, ctypes.byref(pp), len(der))
        if not pkey:
            raise ValueError("서명 키를 읽거나 서명을 생성하지 못했습니다.")
        return pkey

    def raw_public_key(self, pkey: ctypes.c_void_p) -> bytes:
        out = ctypes.create_string_buffer(64)
        out_len = ctypes.c_size_t(len(out))
        if self.crypto.EVP_PKEY_get_raw_public_key(pkey, out, ctypes.byref(out_len)) != 1:
            raise ValueError("개인키에서 공개키를 읽지 못했습니다.")
        return bytes(out.raw[:out_len.value])

    def sign(self, pkey: ctypes.c_void_p, message: bytes) -> bytes:
        ctx = self.crypto.EVP_MD_CTX_new()
        if not ctx:
            raise ValueError("서명 컨텍스트를 만들지 못했습니다.")
        try:
            if self.crypto.EVP_DigestSignInit(ctx, None, None, None, pkey) != 1:
                raise ValueError("서명을 초기화하지 못했습니다.")
            msg = ctypes.create_string_buffer(message)
            sig_len = ctypes.c_size_t(0)
            if self.crypto.EVP_DigestSign(ctx, None, ctypes.byref(sig_len), msg, len(message)) != 1:
                raise ValueError("서명 길이를 계산하지 못했습니다.")
            sig = ctypes.create_string_buffer(sig_len.value)
            if self.crypto.EVP_DigestSign(ctx, sig, ctypes.byref(sig_len), msg, len(message)) != 1:
                raise ValueError("서명을 생성하지 못했습니다.")
            return bytes(sig.raw[:sig_len.value])
        finally:
            self.crypto.EVP_MD_CTX_free(ctx)


def _sign_with_libcrypto(private_key: Path, public_key: bytes, message: bytes) -> bytes | None:
    """Sign via the bundled libcrypto-3.dll. Returns None when unavailable."""
    try:
        crypto = _Libcrypto()
    except (OSError, AttributeError):
        return None
    pkey = crypto.load_private_key(private_key)
    try:
        if crypto.raw_public_key(pkey) != public_key:
            raise ValueError("개인키가 션에 고정된 Ed25519 공개키와 일치하지 않습니다.")
        return crypto.sign(pkey, message)
    finally:
        crypto.crypto.EVP_PKEY_free(pkey)


def _sign_with_openssl_cli(private_key: Path, public_key: bytes, message: bytes,
                           openssl: str) -> bytes:
    der = _openssl([openssl, "pkey", "-in", str(private_key), "-pubout", "-outform", "DER"])
    if der != _ED25519_SPKI_PREFIX + public_key:
        raise ValueError("개인키가 션에 고정된 Ed25519 공개키와 일치하지 않습니다.")
    with tempfile.TemporaryDirectory(prefix="syeon-sign-") as tmp:
        msg_path = Path(tmp) / "manifest.json"
        sig_path = Path(tmp) / "manifest.sig"
        msg_path.write_bytes(message)
        _openssl([openssl, "pkeyutl", "-sign", "-inkey", str(private_key), "-rawin",
                  "-in", str(msg_path), "-out", str(sig_path)])
        return sig_path.read_bytes()


def sign_update(archive: Path, private_key: Path, output_dir: Path,
                openssl: str = "openssl", public_key: bytes = PUBLIC_KEY_BYTES) -> tuple[Path, Path]:
    """Create the exact signed manifest and raw detached signature, once."""
    archive = Path(archive)
    private_key = Path(private_key)
    output_dir = Path(output_dir)
    match = _NAME.fullmatch(archive.name)
    if not match or len(match.group(1)) > 32:
        raise ValueError("릴리스 파일명은 SyeonPet-X.Y.Z-windows.zip이어야 합니다.")
    if len(public_key) != 32:
        raise ValueError("Ed25519 공개키 형식이 올바르지 않습니다.")
    if not archive.is_file() or not private_key.is_file() or not output_dir.is_dir():
        raise ValueError("릴리스 파일, 개인키, 출력 폴더를 확인해 주세요.")
    version = match.group(1)
    manifest_path = output_dir / f"SyeonPet-{version}-update.json"
    signature_path = output_dir / f"SyeonPet-{version}-update.sig"
    if manifest_path.exists() or signature_path.exists():
        raise FileExistsError("서명 출력 파일이 이미 있습니다. 새 폴더를 사용하세요.")

    size, digest = updater_core._hash_file(archive, updater_core._MAX_ARCHIVE)
    if size <= 0:
        raise ValueError("빈 릴리스 파일에는 서명할 수 없습니다.")
    document = {"schema": 1, "platform": "windows-x64", "version": version,
                "asset": archive.name, "size": size, "sha256": digest}
    # Apply the same ZIP and package-manifest checks the installed app will use.
    updater_core._verify_archive(archive, document)
    raw = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    signed = _sign_with_libcrypto(private_key, public_key, raw)
    if signed is None:
        signed = _sign_with_openssl_cli(private_key, public_key, raw, openssl)
    updater_core.verify_signature(public_key, raw, signed)
    if updater_core._hash_file(archive, updater_core._MAX_ARCHIVE) != (size, digest):
        raise ValueError("서명 중 릴리스 파일이 변경되었습니다.")

    created_manifest = False
    created_signature = False
    try:
        with manifest_path.open("xb") as output:
            created_manifest = True
            output.write(raw)
        with signature_path.open("xb") as output:
            created_signature = True
            output.write(signed)
    except BaseException:
        if created_signature:
            signature_path.unlink(missing_ok=True)
        if created_manifest:
            manifest_path.unlink(missing_ok=True)
        raise
    return manifest_path, signature_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--openssl", default="openssl", help="OpenSSL 3 실행 파일 경로")
    args = parser.parse_args()
    try:
        manifest, signature = sign_update(args.archive, args.private_key, args.output_dir, args.openssl)
    except (ValueError, OSError) as exc:
        print(f"업데이트 서명 실패: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"manifest": str(manifest), "signature": str(signature)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
