"""Inspect the existing native launcher using a synthetic isolated main.py.

Never executes the original checkout's main.py, reads user settings, or sends
network requests. Intended for local release verification on Windows only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "SyeonPet.exe", "python312.dll", "python3.dll", "python312.zip",
    "python312._pth", "vcruntime140.dll", "vcruntime140_1.dll",
)


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("Windows is required")
    with tempfile.TemporaryDirectory(prefix="syeon-launcher-check-") as tmp:
        directory = Path(tmp)
        for name in FILES:
            shutil.copy2(ROOT / name, directory / name)
        (directory / "main.py").write_text(
            "import json, os, pathlib, sys\n"
            "pathlib.Path(__file__).with_name('marker.json').write_text(\n"
            "    json.dumps({'file': __file__, 'cwd': os.getcwd(), "
            "'exe': sys.executable}), encoding='utf-8')\n", encoding="utf-8")
        process = subprocess.run([str(directory / "SyeonPet.exe")],
                                 cwd=ROOT.parent, timeout=20, check=False,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        marker = directory / "marker.json"
        if not marker.is_file():
            raise RuntimeError(f"Synthetic main.py did not execute (exit={process.returncode})")
        state = json.loads(marker.read_text(encoding="utf-8"))
        actual = Path(state["file"])
        if not actual.is_absolute():
            actual = Path(state["cwd"]) / actual
        if actual.resolve() != (directory / "main.py").resolve():
            raise RuntimeError(f"Launcher executed an unexpected file: {state}")
        print("PASS: isolated launcher executed copied synthetic main.py")
        print("exit=", process.returncode, "cwd=", state["cwd"])


if __name__ == "__main__":
    main()
