"""Exercise release signing using temporary, unrelated test keys only."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

import updater_core
from tools.sign_update import sign_update


def make_archive(path: Path) -> None:
    files = {"SyeonPet.exe": b"test launcher", "pythonw.exe": b"test python",
             "main.py": b"test main", "version.py": b"test version", "config.json": b"{}"}
    manifest = {"version": "9.8.7", "source_commit": "f" * 40,
                "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        archive.writestr("package-manifest.json", json.dumps(manifest).encode())


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required for release signing")
class SignUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="syeon-sign-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root / "test-key.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(self.key)],
                       check=True, capture_output=True)
        der = subprocess.run(["openssl", "pkey", "-in", str(self.key), "-pubout", "-outform", "DER"],
                             check=True, capture_output=True).stdout
        self.public_key = der[-32:]
        self.archive = self.root / "SyeonPet-9.8.7-windows.zip"
        make_archive(self.archive)

    def test_signature_is_verified_by_updater_and_does_not_overwrite(self):
        manifest, signature = sign_update(self.archive, self.key, self.root, public_key=self.public_key)
        raw, sig = manifest.read_bytes(), signature.read_bytes()
        self.assertEqual(len(sig), 64)
        metadata = updater_core.parse_signed_manifest(raw, sig, self.public_key, "9.8.7")
        self.assertEqual(metadata["sha256"], hashlib.sha256(self.archive.read_bytes()).hexdigest())
        with self.assertRaises(updater_core.UpdateError):
            updater_core.parse_signed_manifest(raw + b" ", sig, self.public_key)
        with self.assertRaises(FileExistsError):
            sign_update(self.archive, self.key, self.root, public_key=self.public_key)
        self.assertEqual(manifest.read_bytes(), raw)
        self.assertEqual(signature.read_bytes(), sig)

    def test_wrong_key_and_invalid_archive_do_not_create_assets(self):
        with self.assertRaises(ValueError):
            sign_update(self.archive, self.key, self.root, public_key=b"x" * 32)
        self.archive.write_bytes(b"not a zip")
        with self.assertRaises(updater_core.UpdateError):
            sign_update(self.archive, self.key, self.root, public_key=self.public_key)
        self.assertFalse((self.root / "SyeonPet-9.8.7-update.json").exists())
        self.assertFalse((self.root / "SyeonPet-9.8.7-update.sig").exists())

    def test_incorrect_version_or_release_name_rejected(self):
        renamed = self.root / "SyeonPet-9.8.8-windows.zip"
        shutil.copyfile(self.archive, renamed)
        with self.assertRaises(updater_core.UpdateError):
            sign_update(renamed, self.key, self.root, public_key=self.public_key)
        renamed = self.root / "SyeonPet-9.8.7-beta-windows.zip"
        shutil.copyfile(self.archive, renamed)
        with self.assertRaises(ValueError):
            sign_update(renamed, self.key, self.root, public_key=self.public_key)


if __name__ == "__main__":
    unittest.main()
