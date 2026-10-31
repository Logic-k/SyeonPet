"""Run repository regressions with temporary user state and loopback-only networking."""
from contextlib import ExitStack
import ipaddress
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "Lib/site-packages"), str(ROOT / "tests")]
REAL_DNS = socket.getaddrinfo
REAL_CONNECT = socket.socket.connect


def local_dns(host, *args, **kwargs):
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise socket.gaierror(socket.EAI_NONAME, "Tests block external DNS")
    return REAL_DNS(host, *args, **kwargs)


def local_connect(connection, address):
    try:
        allowed = ipaddress.ip_address(address[0]).is_loopback
    except ValueError:
        allowed = False
    if not allowed:
        raise OSError("Tests block external TCP")
    return REAL_CONNECT(connection, address)


def run():
    with tempfile.TemporaryDirectory(prefix="syeon-tests-") as temporary, ExitStack() as stack:
        data = Path(temporary)
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["LOCALAPPDATA"] = str(data / "local")
        os.environ["APPDATA"] = str(data / "roaming")
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        import chat
        import main as entry
        import optimizer_library
        import pet
        import wardrobe

        app = QApplication.instance() or QApplication([])

        def settings_factory(organization, application):
            if isinstance(application, str):
                settings = QSettings(str(data / (application + ".ini")), QSettings.Format.IniFormat)
                settings.setFallbacksEnabled(False)
                return settings
            return QSettings(organization, application)

        for module in (chat, entry, pet, wardrobe):
            if hasattr(module, "QSettings"):
                stack.enter_context(patch.object(module, "QSettings", side_effect=settings_factory))
        for module in (pet, wardrobe):
            stack.enter_context(patch.object(module, "pack_root", return_value=data / "packs"))
        stack.enter_context(patch.object(optimizer_library.QStandardPaths, "writableLocation",
                                        return_value=str(data / "appdata")))
        clipboard_state = {"text": ""}
        clipboard = SimpleNamespace(setText=lambda value: clipboard_state.__setitem__("text", value),
                                    text=lambda: clipboard_state["text"])
        stack.enter_context(patch.object(QApplication, "clipboard", return_value=clipboard))
        stack.enter_context(patch.object(socket, "getaddrinfo", side_effect=local_dns))
        stack.enter_context(patch.object(socket.socket, "connect", new=local_connect))
        suite = (unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:])
                 if sys.argv[1:] else unittest.defaultTestLoader.discover(str(ROOT / "tests")))
        print(f"QA_SUITE_COUNT={suite.countTestCases()}", flush=True)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        app.closeAllWindows()
        app.processEvents()
        print(f"QA_RESULT tests={result.testsRun} failures={len(result.failures)} "
              f"errors={len(result.errors)} skipped={len(result.skipped)}", flush=True)
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(run())
