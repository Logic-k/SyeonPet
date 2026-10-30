"""Sign a verified portable release for the built-in Windows updater.

The Ed25519 private key is supplied by path and must remain outside the
repository and release artifacts. OpenSSL 3 is required on the build machine.
"""
from __future__ import annotations

import argparse
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

    der = _openssl([openssl, "pkey", "-in", str(private_key), "-pubout", "-outform", "DER"])
    if der != _ED25519_SPKI_PREFIX + public_key:
        raise ValueError("개인키가 션에 고정된 Ed25519 공개키와 일치하지 않습니다.")

    size, digest = updater_core._hash_file(archive, updater_core._MAX_ARCHIVE)
    if size <= 0:
        raise ValueError("빈 릴리스 파일에는 서명할 수 없습니다.")
    document = {"schema": 1, "platform": "windows-x64", "version": version,
                "asset": archive.name, "size": size, "sha256": digest}
    # Apply the same ZIP and package-manifest checks the installed app will use.
    updater_core._verify_archive(archive, document)
    raw = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="syeon-sign-") as tmp:
        message = Path(tmp) / "manifest.json"
        signature = Path(tmp) / "manifest.sig"
        message.write_bytes(raw)
        _openssl([openssl, "pkeyutl", "-sign", "-inkey", str(private_key), "-rawin",
                  "-in", str(message), "-out", str(signature)])
        signed = signature.read_bytes()
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
