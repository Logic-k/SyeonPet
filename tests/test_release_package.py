"""Packaging checks; do not launch a production app or modify its settings."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("syeon_build_release", ROOT / "tools/build_release.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ReleasePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="syeon-release-test-")
        cls.outdir = Path(cls.temp.name)
        cls.snapshot = cls.outdir / "snapshot"
        # Other agents may be editing source concurrently. Copy one explicit
        # input snapshot so the reproducibility check compares identical bytes.
        for source in set(builder.collect_files(ROOT).values()):
            relative = source.relative_to(ROOT)
            target = cls.snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (cls.snapshot / "Lib/site-packages/PySide6/user-private.json").write_text(
            '{"sentinel":"do-not-distribute"}', encoding="utf-8")
        cls.first = cls.outdir / "first.zip"
        cls.second = cls.outdir / "second.zip"
        cls.version = builder._source_version(cls.snapshot)
        cls.build1 = builder.build(cls.snapshot, cls.first, cls.version)
        cls.build2 = builder.build(cls.snapshot, cls.second, cls.version)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_archive_is_reproducible_and_has_own_checksum(self):
        self.assertEqual(self.build1["sha256"], self.build2["sha256"])
        self.assertEqual(self.build1["sha256"], hashlib.sha256(self.first.read_bytes()).hexdigest())
        self.assertTrue(Path(str(self.first) + ".sha256").read_text(encoding="utf-8").startswith(self.build1["sha256"]))

    def test_only_explicit_runtime_data_and_sanitized_config(self):
        with zipfile.ZipFile(self.first) as archive:
            names = set(archive.namelist())
            for name in ("SyeonPet.exe", "pythonw.exe", "python312.zip", "main.py", "run.cmd",
                         "update_checker.py", "version.py", "assets/manifest.json",
                         "assets/prompt_templates.json", "assets/seobu_office_templates.json",
                         "assets/thinking/manifest.json", "assets/thinking/sprite-sheet-alpha.png",
                         "Lib/site-packages/PySide6/QtWidgets.pyd",
                         "Lib/site-packages/PySide6/plugins/platforms/qwindows.dll",
                         "licenses/PYTHON-3.12.7.txt", "licenses/LGPL-3.0-only.txt"):
                self.assertIn(name, names)
            self.assertFalse(any(name.startswith((".git/", "docs/", "tests/", "local-runtime/"))
                                 or "__pycache__" in name or name.endswith("library.json")
                                 for name in names))
            self.assertNotIn("assets/thinking/source-strip.png", names)
            self.assertNotIn("assets/thinking/reference.png", names)
            self.assertNotIn("Lib/site-packages/PySide6/user-private.json", names)
            config = json.loads(archive.read("config.json"))
            self.assertNotIn("chat", config)
            self.assertNotIn("api_key", json.dumps(config).lower())
            self.assertEqual(archive.read("config.json"), (self.snapshot / "tools/default-config.json").read_bytes())
            manifest = json.loads(archive.read("package-manifest.json"))
            self.assertEqual(manifest["version"], self.version)
            self.assertIsNone(manifest["source_commit"])
            for name, expected in manifest["files"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected, name)

    def test_refuses_overwrite_and_wrong_version(self):
        before = self.first.read_bytes()
        with self.assertRaises(FileExistsError):
            builder.build(ROOT, self.first, self.version)
        self.assertEqual(self.first.read_bytes(), before)
        with self.assertRaises(ValueError):
            builder.build(ROOT, self.outdir / "wrong.zip", "999.0.0")
        self.assertFalse((self.outdir / "wrong.zip").exists())

    def test_zip_path_must_be_outside_checkout(self):
        with self.assertRaises(ValueError):
            builder.build(ROOT, ROOT / "test-artifact.zip", self.version)

    def test_git_commit_blob_is_lf_even_when_worktree_is_crlf(self):
        repo = self.outdir / "crlf-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=repo, check=True)
        (repo / "tools").mkdir()
        (repo / "tools/build_release.py").write_bytes(b"# script\r\n")
        (repo / "run.cmd").write_bytes(b"@echo off\r\necho hello\r\n")
        subprocess.run(["git", "add", "--", "tools/build_release.py", "run.cmd"], cwd=repo,
                       check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=QA", "-c", "user.email=qa@example.invalid",
                        "-c", "core.hooksPath=none", "commit", "-qm", "synthetic"], cwd=repo,
                       check=True, capture_output=True)
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                                  capture_output=True, text=True).stdout.strip()
        blobs = builder._revision_blobs(repo, revision, {"run.cmd": repo / "run.cmd"})
        committed = builder._committed_bytes(repo, blobs)
        self.assertEqual(committed[blobs["run.cmd"]], b"@echo off\necho hello\n")
        self.assertEqual((repo / "run.cmd").read_bytes(), b"@echo off\r\necho hello\r\n")
        self.assertEqual(builder._builder_git_hash(repo), blobs["tools/build_release.py"])

    def test_revision_archive_uses_commit_bytes_not_worktree(self):
        repo = self.outdir / "mock-commit"
        repo.mkdir()
        (repo / "version.py").write_bytes(b'APP_VERSION = "0.1.0"\r\n')
        (repo / "run.cmd").write_bytes(b"@echo off\r\necho private-working-change\r\n")
        files = {"version.py": repo / "version.py", "run.cmd": repo / "run.cmd"}
        data = {"version.py": b'APP_VERSION = "0.1.0"\n', "run.cmd": b"@echo off\necho committed\n"}
        blobs = {name: builder._git_blob(content) for name, content in data.items()}
        blobs["tools/build_release.py"] = "b" * 40
        committed = {blobs[name]: value for name, value in data.items()}
        output = self.outdir / "commit.zip"
        with patch.object(builder, "collect_files", return_value=files), \
                patch.object(builder, "APP_FILES", ("version.py",)), \
                patch.object(builder, "_revision_blobs", return_value=blobs), \
                patch.object(builder, "_committed_bytes", return_value=committed), \
                patch.object(builder, "_revision_local_modules", return_value={"version.py"}), \
                patch.object(builder, "_builder_git_hash", return_value="b" * 40):
            builder.build(repo, output, "0.1.0", "a" * 40)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read("run.cmd"), data["run.cmd"])
            self.assertNotIn(b"private-working-change", archive.read("run.cmd"))
            self.assertEqual(json.loads(archive.read("package-manifest.json"))["source_commit"], "a" * 40)

    def test_revision_import_audit_uses_commit_sources(self):
        repo = self.outdir / "import-audit"
        repo.mkdir()
        (repo / "version.py").write_text('APP_VERSION = "0.1.0"\n', encoding="utf-8")
        (repo / "main.py").write_text("pass\n", encoding="utf-8")
        files = {name: repo / name for name in ("version.py", "main.py")}
        data = {"version.py": b'APP_VERSION = "0.1.0"\n', "main.py": b"import hidden_local\n"}
        blobs = {name: builder._git_blob(value) for name, value in data.items()}
        blobs["tools/build_release.py"] = "b" * 40
        committed = {blobs[name]: value for name, value in data.items()}
        output = self.outdir / "import-audit.zip"
        with patch.object(builder, "collect_files", return_value=files), \
                patch.object(builder, "APP_FILES", ("version.py", "main.py")), \
                patch.object(builder, "_revision_blobs", return_value=blobs), \
                patch.object(builder, "_committed_bytes", return_value=committed), \
                patch.object(builder, "_revision_local_modules", return_value={"version.py", "main.py", "hidden_local.py"}), \
                patch.object(builder, "_builder_git_hash", return_value="b" * 40):
            with self.assertRaisesRegex(ValueError, "hidden_local.py"):
                builder.build(repo, output, "0.1.0", "a" * 40)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
