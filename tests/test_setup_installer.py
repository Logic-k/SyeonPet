"""Unit and integration tests for Zero-Admin Native Setup .exe Builder."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import tools.build_release as release_builder
import tools.build_setup as setup_builder


class SetupInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="syeon-setup-test-")
        cls.work_dir = Path(cls.temp_dir.name)

        cls.version = "0.2.0"
        cls.zip_output = cls.work_dir / f"SyeonPet-{cls.version}-windows.zip"
        cls.setup_output = cls.work_dir / f"SyeonPet-Setup-{cls.version}.exe"

        # Build portable release zip into tempdir
        cls.zip_res = release_builder.build(ROOT, cls.zip_output, cls.version)

        # Build setup exe
        cls.setup_res = setup_builder.build_setup(cls.zip_output, cls.setup_output, cls.version)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_setup_compilation_and_sha256_checksum(self):
        self.assertTrue(self.setup_output.is_file())
        self.assertTrue(self.setup_res["size"] > 1_000_000)

        # Verify sha256 checksum file
        checksum_file = Path(str(self.setup_output) + ".sha256")
        self.assertTrue(checksum_file.is_file())
        content = checksum_file.read_text(encoding="utf-8").strip()
        expected = hashlib.sha256(self.setup_output.read_bytes()).hexdigest()
        self.assertEqual(self.setup_res["sha256"], expected)
        self.assertTrue(content.startswith(expected))
        self.assertIn(self.setup_output.name, content)

    def test_validation_errors(self):
        with self.assertRaises(ValueError):
            # Output path must be .exe
            setup_builder.build_setup(self.zip_output, self.work_dir / "invalid.bin", self.version)

        with self.assertRaises(ValueError):
            # Output path must be outside ROOT
            setup_builder.build_setup(self.zip_output, ROOT / "setup-in-root.exe", self.version)

        with self.assertRaises(ValueError):
            # Version mismatch
            setup_builder.build_setup(self.zip_output, self.work_dir / "wrong_ver.exe", "9.9.9")

    def test_silent_installation_and_clean_shortcuts(self):
        install_dir = self.work_dir / "install"
        desktop_dir = self.work_dir / "desktop"
        startmenu_dir = self.work_dir / "startmenu"

        install_dir.mkdir(parents=True, exist_ok=True)
        desktop_dir.mkdir(parents=True, exist_ok=True)
        startmenu_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(self.setup_output),
            "--silent",
            "--target-dir", str(install_dir),
            "--desktop-dir", str(desktop_dir),
            "--startmenu-dir", str(startmenu_dir),
            "--no-launch",
            "--no-registry",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(res.returncode, 0, f"Setup failed: {res.stdout} {res.stderr}")

        # Assert extracted core runtime files exist
        self.assertTrue((install_dir / "SyeonPet.exe").is_file())
        self.assertTrue((install_dir / "main.py").is_file())
        self.assertTrue((install_dir / "version.py").is_file())
        self.assertTrue((install_dir / "syeonpet" / "finder" / "index_engine.py").is_file())
        self.assertTrue((install_dir / "syeonpet" / "chat" / "direct_work_ui.py").is_file())
        self.assertTrue((install_dir / "package-manifest.json").is_file())
        self.assertTrue((install_dir / "사용안내.txt").is_file())
        self.assertTrue((install_dir / "션_삭제하기.cmd").is_file())

        # Assert shortcuts exist and have valid LNK magic header
        desktop_lnk = desktop_dir / "션 데스크탑 펫.lnk"
        start_lnk = startmenu_dir / "션 데스크탑 펫.lnk"
        self.assertTrue(desktop_lnk.is_file())
        self.assertTrue(start_lnk.is_file())

        desktop_bytes = desktop_lnk.read_bytes()
        start_bytes = start_lnk.read_bytes()
        self.assertTrue(len(desktop_bytes) > 0)
        self.assertTrue(len(start_bytes) > 0)
        self.assertEqual(desktop_bytes[:4], b"L\x00\x00\x00")
        self.assertEqual(start_bytes[:4], b"L\x00\x00\x00")

    def test_preserves_custom_user_config_on_reinstall(self):
        install_dir = self.work_dir / "reinstall_test"
        desktop_dir = self.work_dir / "reinstall_desktop"
        startmenu_dir = self.work_dir / "reinstall_startmenu"

        install_dir.mkdir(parents=True, exist_ok=True)
        desktop_dir.mkdir(parents=True, exist_ok=True)
        startmenu_dir.mkdir(parents=True, exist_ok=True)

        config_path = install_dir / "config.json"

        # Pre-create config with custom values
        custom_config = {"display_size": 250, "walk_speed": 123, "custom_sentinel": "preserved_ok"}
        config_path.write_text(json.dumps(custom_config, ensure_ascii=False), encoding="utf-8")

        # Run setup over the directory
        cmd = [
            str(self.setup_output),
            "--silent",
            "--target-dir", str(install_dir),
            "--desktop-dir", str(desktop_dir),
            "--startmenu-dir", str(startmenu_dir),
            "--no-launch",
            "--no-registry",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(res.returncode, 0, f"Setup failed: {res.stdout} {res.stderr}")

        # Check config.json still has custom values
        reloaded = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(reloaded.get("walk_speed"), 123)
        self.assertEqual(reloaded.get("display_size"), 250)
        self.assertEqual(reloaded.get("custom_sentinel"), "preserved_ok")


if __name__ == "__main__":
    unittest.main()
