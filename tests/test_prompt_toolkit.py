"""Tests for executable selection and launch without requiring a GUI."""
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class FakeSettings:
    class Status:
        NoError = 0

    def __init__(self, *_):
        self.values = {}

    def value(self, key, default, type=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        pass

    def status(self):
        return self.Status.NoError


core = types.ModuleType("PySide6.QtCore")
core.QSettings = FakeSettings
core.QProcess = MagicMock()
widgets = types.ModuleType("PySide6.QtWidgets")
widgets.QFileDialog = MagicMock()
widgets.QMessageBox = MagicMock()
with patch.dict(sys.modules, {"PySide6": types.ModuleType("PySide6"),
                              "PySide6.QtCore": core,
                              "PySide6.QtWidgets": widgets}):
    spec = importlib.util.spec_from_file_location(
        "prompt_toolkit_under_test", Path(__file__).resolve().parents[1] / "prompt_toolkit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="션 툴킷 ")
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "프롬프트 툴킷.exe"
        self.path.touch()
        self.launcher = module.PromptToolkitLauncher()
        widgets.QFileDialog.reset_mock()
        widgets.QMessageBox.reset_mock()
        core.QProcess.reset_mock()
        core.QProcess.startDetached.return_value = (True, 123)

    def test_selection_and_repeated_launch(self):
        widgets.QFileDialog.getOpenFileName.return_value = (str(self.path), "")
        self.assertTrue(self.launcher.open())
        self.assertEqual(self.launcher._settings.values["executablePath"], str(self.path.resolve()))
        self.assertTrue(self.launcher.open())
        self.assertEqual(core.QProcess.startDetached.call_count, 2)
        core.QProcess.startDetached.assert_called_with(str(self.path), [], str(self.path.parent))

    def test_invalid_selection_preserves_previous_setting(self):
        self.launcher._settings.setValue("executablePath", str(self.path))
        widgets.QFileDialog.getOpenFileName.return_value = (str(self.path.with_suffix(".txt")), "")
        self.assertFalse(self.launcher.configure())
        self.assertEqual(self.launcher._settings.values["executablePath"], str(self.path))
        widgets.QMessageBox.warning.assert_called_once()

    def test_missing_configured_file_does_not_launch_when_cancelled(self):
        self.launcher._settings.setValue("executablePath", str(self.path.with_name("missing.exe")))
        widgets.QFileDialog.getOpenFileName.return_value = ("", "")
        self.assertFalse(self.launcher.open())
        core.QProcess.startDetached.assert_not_called()
        widgets.QMessageBox.warning.assert_called_once()

    def test_start_failure_gives_guidance(self):
        self.launcher._settings.setValue("executablePath", str(self.path))
        core.QProcess.startDetached.return_value = (False, 0)
        self.assertFalse(self.launcher.open())
        self.assertIn("WebView2", widgets.QMessageBox.warning.call_args.args[2])

    def test_optional_side_by_side_distribution(self):
        default = Path(self.folder.name) / "prompt-toolkit-desktop.exe"
        default.touch()
        with patch.object(self.launcher, "_application_dir", return_value=default.parent):
            self.assertTrue(self.launcher.open())
        core.QProcess.startDetached.assert_called_once_with(str(default), [], str(default.parent))
        widgets.QFileDialog.getOpenFileName.assert_not_called()


if __name__ == "__main__":
    unittest.main()
