"""Windows Desktop and Start Menu shortcut creation helper.

Creates reliable .lnk shortcuts with verified target executables, working directory,
and icon paths via Windows Script Host COM, passing paths via environment variables
to completely eliminate Korean path escaping and encoding corruption.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess
import sys

from syeonpet.paths import ASSETS_DIR, ICON_PATH, get_install_root

SHORTCUT_NAME: str = "션 데스크탑 펫.lnk"
SHORTCUT_DESC: str = "션 데스크탑 펫 (Syeon Desktop Pet)"

POWERSHELL_SCRIPT: str = """
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($env:SYEON_SHORTCUT_PATH)
$s.TargetPath = $env:SYEON_TARGET_PATH
$s.Arguments = $env:SYEON_ARGUMENTS
$s.WorkingDirectory = $env:SYEON_WORKDIR
if ($env:SYEON_ICON -and (Test-Path $env:SYEON_ICON)) {
    $s.IconLocation = $env:SYEON_ICON
}
$s.Description = $env:SYEON_DESC
$s.Save()
"""


def get_desktop_dir() -> Path:
    """Return the user's Desktop directory."""
    profile = os.environ.get("USERPROFILE")
    if profile and profile.strip():
        cand = Path(profile.strip()) / "Desktop"
        if cand.is_dir():
            return cand.resolve()
    return (Path.home() / "Desktop").resolve()


def get_start_menu_dir() -> Path:
    """Return the user's Start Menu Programs directory."""
    appdata = os.environ.get("APPDATA")
    if appdata and appdata.strip():
        cand = Path(appdata.strip()) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if cand.is_dir():
            return cand.resolve()
    return (Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs").resolve()


def resolve_launcher_target(install_root: Path | None = None) -> dict[str, str]:
    """Resolve target executable, launch arguments, workdir, and icon in install_root."""
    root = get_install_root(install_root)
    exe_candidate = root / "SyeonPet.exe"
    pythonw_candidate = root / "pythonw.exe"

    if exe_candidate.is_file():
        target_path = str(exe_candidate)
        arguments = ""
    elif pythonw_candidate.is_file():
        target_path = str(pythonw_candidate)
        arguments = "main.py"
    else:
        target_path = sys.executable
        arguments = "main.py" if (root / "main.py").is_file() else ""

    icon_cand = root / "assets" / "icon.ico"
    icon = str(icon_cand) if icon_cand.is_file() else (str(ICON_PATH) if ICON_PATH.is_file() else "")

    return {
        "target_path": target_path,
        "arguments": arguments,
        "workdir": str(root),
        "icon": icon,
        "description": SHORTCUT_DESC,
    }


def create_shortcut(
    shortcut_path: Path,
    target_path: str,
    arguments: str = "",
    workdir: str = "",
    icon_path: str = "",
    description: str = SHORTCUT_DESC,
    dry_run: bool = False,
) -> Path:
    """Create a Windows .lnk shortcut cleanly via PowerShell COM without encoding issues."""
    shortcut_path = Path(shortcut_path).resolve()
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        return shortcut_path

    if sys.platform == "win32":
        env = os.environ.copy()
        env["SYEON_SHORTCUT_PATH"] = str(shortcut_path)
        env["SYEON_TARGET_PATH"] = str(target_path)
        env["SYEON_ARGUMENTS"] = str(arguments)
        env["SYEON_WORKDIR"] = str(workdir)
        env["SYEON_ICON"] = str(icon_path) if icon_path else ""
        env["SYEON_DESC"] = str(description)

        encoded_cmd = base64.b64encode(POWERSHELL_SCRIPT.encode("utf-16le")).decode("ascii")
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            encoded_cmd,
        ]
        result = subprocess.run(cmd, env=env, text=True, capture_output=True)
        if result.returncode != 0 or not shortcut_path.is_file():
            raise RuntimeError(
                f"Failed to create shortcut at {shortcut_path}: rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
            )
    else:
        # Non-Windows stub for headless CI/testing
        shortcut_path.write_bytes(b"STUB_SHORTCUT")

    return shortcut_path


def create_desktop_shortcut(
    install_root: Path | None = None,
    target_dir: Path | None = None,
    dry_run: bool = False,
) -> Path:
    """Create a shortcut on the user's Desktop or custom target_dir."""
    dest_dir = Path(target_dir).resolve() if target_dir else get_desktop_dir()
    shortcut_path = dest_dir / SHORTCUT_NAME
    target = resolve_launcher_target(install_root)
    return create_shortcut(
        shortcut_path=shortcut_path,
        target_path=target["target_path"],
        arguments=target["arguments"],
        workdir=target["workdir"],
        icon_path=target["icon"],
        description=target["description"],
        dry_run=dry_run,
    )


def create_start_menu_shortcut(
    install_root: Path | None = None,
    target_dir: Path | None = None,
    dry_run: bool = False,
) -> Path:
    """Create a shortcut in the user's Start Menu Programs or custom target_dir."""
    dest_dir = Path(target_dir).resolve() if target_dir else get_start_menu_dir()
    shortcut_path = dest_dir / SHORTCUT_NAME
    target = resolve_launcher_target(install_root)
    return create_shortcut(
        shortcut_path=shortcut_path,
        target_path=target["target_path"],
        arguments=target["arguments"],
        workdir=target["workdir"],
        icon_path=target["icon"],
        description=target["description"],
        dry_run=dry_run,
    )
