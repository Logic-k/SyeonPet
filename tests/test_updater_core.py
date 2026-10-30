"""Signed updater tests; all files and network responses are synthetic."""
from __future__ import annotations

import hashlib
import io
import json
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import updater_core as updater


def _archive(version="0.1.6", extras=None):
    files = {"SyeonPet.exe": b"synthetic launcher", "pythonw.exe": b"synthetic python",
             "main.py": b"synthetic main", "config.json": b"{}", "version.py": b"synthetic version"}
    files.update(extras or {})
    manifest = {"version": version, "source_commit": "f" * 40,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.writestr("package-manifest.json", json.dumps(manifest).encode())
    return out.getvalue()


def _manifest(archive, version="0.1.6"):
    return json.dumps({"schema": 1, "platform": "windows-x64", "version": version,
                       "asset": f"SyeonPet-{version}-windows.zip", "size": len(archive),
                       "sha256": hashlib.sha256(archive).hexdigest()}, separators=(",", ":")).encode()


def _release(version="0.1.6"):
    names = updater._asset_names(version)
    return json.dumps({"tag_name": "v" + version, "draft": False, "prerelease": False,
                       "html_url": "https://github.com/Logic-k/SyeonPet/releases/tag/v" + version,
                       "assets": [{"name": n, "state": "uploaded",
                                   "browser_download_url": updater.RELEASE_ROOT + "v" + version + "/" + n}
                                  for n in names]}).encode()


class SignatureTests(unittest.TestCase):
    def test_rfc8032_ed25519_vector_and_tampering(self):
        # RFC 8032 section 7.1, test 1, empty message.
        key = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
                            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
        updater.verify_signature(key, b"", sig)
        with self.assertRaises(updater.UpdateError):
            updater.verify_signature(key, b"tampered", sig)

    def test_rejects_invalid_key_lengths(self):
        for key, sig in ((b"", b"x" * 64), (b"x" * 32, b"")):
            with self.subTest(key=len(key), sig=len(sig)), self.assertRaises(updater.UpdateError):
                updater.verify_signature(key, b"data", sig)

    def test_release_public_key_fixture_and_real_bundled_python(self):
        # A harmless message signed by the release key checks that the pinned
        # public key and the packaged OpenSSL DLL agree; no private key is read.
        from update_trust import PUBLIC_KEY_BYTES
        message = b"SyeonPet-update-signing-test-v1\n"
        signature = bytes.fromhex(
            "40e0d9be11e93617785a3e307eabed285f8aed4ca3b7bb3a9b501cbcb4a7039b"
            "8512d4cb6cdf97a7e7063cb3c58413c58ddfdb11371daff32fb6eac4e19fa007"
        )
        updater.verify_signature(PUBLIC_KEY_BYTES, message, signature)
        with self.assertRaises(updater.UpdateError):
            updater.verify_signature(PUBLIC_KEY_BYTES, message + b"!", signature)
        if sys.platform != "win32":
            self.skipTest("The packaged Python executable is Windows-only")
        root = Path(updater.__file__).resolve().parent
        pythonw = root / "pythonw.exe"
        if not pythonw.is_file():
            self.skipTest("The Windows embedded runtime is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "runtime-ok.txt"
            code = (
                "import sys; sys.path.insert(0, " + repr(str(root)) + "); "
                "from updater_core import verify_signature; "
                "from update_trust import PUBLIC_KEY_BYTES; "
                "verify_signature(PUBLIC_KEY_BYTES, " + repr(message) + ", bytes.fromhex(" + repr(signature.hex()) + ")); "
                "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('ok', encoding='utf-8')"
            )
            result = subprocess.run([str(pythonw), "-c", code], cwd=tmp, timeout=30)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(marker.read_text(encoding="utf-8"), "ok")


class NetworkTests(unittest.TestCase):
    def test_timeout_does_not_write_to_caller_stream_or_start_extra_workers(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(url, limit):
            entered.set()
            release.wait(5)
            return b"late"
        output = io.BytesIO()
        try:
            with patch.object(updater, "_download_body", side_effect=delayed), \
                 patch.object(updater, "_DOWNLOAD_DEADLINE", 0.05):
                with self.assertRaises(updater.UpdateError):
                    updater._download(updater.API_URL, 100, output)
                self.assertTrue(entered.is_set())
                with self.assertRaises(updater.UpdateError):
                    updater._download(updater.API_URL, 100, output)
                self.assertEqual(output.getvalue(), b"")
        finally:
            release.set()
            # The daemon releases the shared lock after returning. Do not let
            # one test leak an outstanding worker into another test.
            self.assertTrue(updater._network_lock.acquire(timeout=2))
            updater._network_lock.release()
        self.assertEqual(output.getvalue(), b"")

    def test_network_failures_are_localized_and_do_not_stage_any_data(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(updater, "_download_body", side_effect=OSError("synthetic offline")):
            with self.assertRaisesRegex(updater.UpdateError, "인터넷"):
                updater.prepare_update("0.1.5", tmp, b"k" * 32)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_storage_errors_are_localized_and_keep_existing_installation(self):
        with patch.object(updater, "_prepare_update", side_effect=OSError("private local path")):
            with self.assertRaisesRegex(updater.UpdateError, "기존 버전은 유지됩니다") as caught:
                updater.prepare_update("0.1.5", "unused", b"k" * 32)
            self.assertNotIn("private local path", str(caught.exception))


class ManifestTests(unittest.TestCase):
    def test_signed_manifest_validates_every_field(self):
        blob = _archive()
        raw = _manifest(blob)
        with patch.object(updater, "verify_signature") as verify:
            self.assertEqual(updater.parse_signed_manifest(raw, b"s" * 64, b"k" * 32, "0.1.6")["size"], len(blob))
            verify.assert_called_once_with(b"k" * 32, raw, b"s" * 64)
            values = json.loads(raw)
            for key, value in (("schema", True), ("platform", "linux"), ("version", "0.1.5"),
                               ("asset", "other.zip"), ("size", 0), ("sha256", "F" * 64)):
                bad = dict(values, **{key: value})
                with self.subTest(key=key), self.assertRaises(updater.UpdateError):
                    updater.parse_signed_manifest(json.dumps(bad).encode(), b"s" * 64, b"k" * 32, "0.1.6")
            with self.assertRaises(updater.UpdateError):
                updater.parse_signed_manifest(b'{"schema":1,"schema":1}', b"s" * 64, b"k" * 32)

    def test_untrusted_redirect_and_release_assets_rejected(self):
        self.assertTrue(updater._safe_https("https://release-assets.githubusercontent.com/file?secret=x", updater._ASSET_HOSTS, True))
        for url in ("http://github.com/x", "https://github.com.evil.example/x", "https://u@github.com/x",
                    "https://github.com:444/x", "https://github.com/x#frag"):
            with self.subTest(url=url):
                self.assertFalse(updater._safe_https(url, updater._ASSET_HOSTS, True))
        doc = json.loads(_release())
        doc["assets"][0]["browser_download_url"] = "https://evil.example/app.zip"
        with self.assertRaises(updater.UpdateError):
            updater._checked_release(json.dumps(doc).encode(), "0.1.5")
        with self.assertRaises(updater.UpdateError):
            updater._checked_release(_release("0.1.6-beta"), "0.1.5")
        self.assertIsNone(updater._checked_release(_release("0.1.6"), "0.1.6")[0])


class ArchiveTests(unittest.TestCase):
    def test_rejects_windows_and_traversal_paths(self):
        for name in ("../evil", "/root", "C:/test", "bad\\file", "foo/../bar", "NUL.txt", "x/trail. ",
                     "x//file", "x/CON/file", "file:stream"):
            with self.subTest(name=name), self.assertRaises(updater.UpdateError):
                updater._safe_name(name)

    def test_case_collision_and_symlink_rejected(self):
        for kind in ("case", "symlink"):
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as z:
                z.writestr("file.py", b"1")
                if kind == "case":
                    z.writestr("FILE.py", b"2")
                else:
                    info = zipfile.ZipInfo("link")
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    z.writestr(info, b"file.py")
            with zipfile.ZipFile(io.BytesIO(out.getvalue())) as z, self.subTest(kind=kind), self.assertRaises(updater.UpdateError):
                updater._archive_entries(z)

    def test_archive_hash_and_internal_manifest(self):
        blob = _archive()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.zip"
            path.write_bytes(blob)
            doc = json.loads(_manifest(blob))
            self.assertIn("main.py", updater._verify_archive(path, doc))
            path.write_bytes(blob + b"tampered")
            with self.assertRaises(updater.UpdateError):
                updater._verify_archive(path, doc)

    def test_incomplete_download_and_oversized_archive_are_rejected(self):
        blob = _archive()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "partial.zip"
            path.write_bytes(blob[:-1])
            with self.assertRaises(updater.UpdateError):
                updater._verify_archive(path, json.loads(_manifest(blob)))
        with zipfile.ZipFile(io.BytesIO(blob)) as z, \
             patch.object(updater, "_MAX_EXPANDED", 1), \
             self.assertRaises(updater.UpdateError):
            updater._archive_entries(z)

    def test_prepare_reverify_and_tampering_never_overwrites(self):
        blob = _archive()
        manifest = _manifest(blob)
        sig = b"s" * 64
        names = updater._asset_names("0.1.6")
        responses = {updater.API_URL: _release(), updater.RELEASE_ROOT + "v0.1.6/" + names[0]: blob,
                     updater.RELEASE_ROOT + "v0.1.6/" + names[1]: manifest,
                     updater.RELEASE_ROOT + "v0.1.6/" + names[2]: sig}
        def fetch(url, limit, output=None):
            value = responses[url]
            if len(value) > limit:
                raise updater.UpdateError("synthetic limit")
            if output is not None:
                output.write(value)
                return None
            return value
        with tempfile.TemporaryDirectory() as tmp, patch.object(updater, "_download", side_effect=fetch), \
             patch.object(updater, "verify_signature"):
            root = Path(tmp)
            existing = root / "user-data.txt"
            existing.write_text("keep", encoding="utf-8")
            result = updater.prepare_update("0.1.5", root, b"k" * 32)
            self.assertEqual(result["status"], "ready")
            stage = root / "versions" / "0.1.6"
            self.assertEqual(Path(result["staged_path"]), stage / "app")
            self.assertEqual(updater.verify_staged(stage, b"k" * 32, "0.1.5")["version"], "0.1.6")
            self.assertEqual(updater.prepare_update("0.1.5", root, b"k" * 32)["status"], "already_ready")
            with self.assertRaises(updater.UpdateError):
                updater.verify_staged(stage, b"k" * 32, "0.1.6")
            extra = stage / "app" / "unexpected.txt"
            extra.write_text("untrusted", encoding="utf-8")
            with self.assertRaises(updater.UpdateError):
                updater.verify_staged(stage, b"k" * 32, "0.1.5")
            extra.unlink()  # created by this test in its isolated directory
            original = (stage / "app" / "version.py").read_bytes()
            (stage / "app" / "version.py").unlink()
            with self.assertRaises(updater.UpdateError):
                updater.verify_staged(stage, b"k" * 32, "0.1.5")
            (stage / "app" / "version.py").write_bytes(original)
            (stage / "app" / "main.py").write_text("tampered", encoding="utf-8")
            with self.assertRaises(updater.UpdateError):
                updater.verify_staged(stage, b"k" * 32, "0.1.5")
            with self.assertRaises(updater.UpdateError):
                updater.prepare_update("0.1.5", root, b"k" * 32)
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")

    @unittest.skipUnless(sys.platform == "win32", "Tests the Windows legacy path limit")
    def test_staging_under_realistic_long_profile_path(self):
        nested = "Lib/site-packages/pyside6-6.11.2.dist-info/licenses/LicenseRef-Qt-Commercial.txt"
        blob = _archive(extras={nested: b"synthetic license"})
        manifest = _manifest(blob)
        names = updater._asset_names("0.1.6")
        responses = {updater.API_URL: _release(), updater.RELEASE_ROOT + "v0.1.6/" + names[0]: blob,
                     updater.RELEASE_ROOT + "v0.1.6/" + names[1]: manifest,
                     updater.RELEASE_ROOT + "v0.1.6/" + names[2]: b"s" * 64}
        def fetch(url, limit, output=None):
            value = responses[url]
            if output is not None:
                output.write(value)
                return None
            return value
        with tempfile.TemporaryDirectory() as tmp, patch.object(updater, "_download", side_effect=fetch), \
             patch.object(updater, "verify_signature"):
            root = Path(tmp)
            padding = 71 - len(str(root)) - 1
            if padding < 1:
                self.skipTest("The test temp path is already longer than the target profile path")
            localappdata = root / ("a" * padding)
            state = localappdata / "SyeonPet" / "updates" / ("b" * 32)
            result = updater.prepare_update("0.1.5", state, b"k" * 32)
            self.assertEqual(result["status"], "ready")
            self.assertEqual((Path(result["staged_path"]) / nested).read_bytes(), b"synthetic license")


if __name__ == "__main__":
    unittest.main()
