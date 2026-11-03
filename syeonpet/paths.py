"""Centralized path definitions and resolution for SyeonPet.

Single source of truth for paths, preserving updater invariants and safe local
storage boundaries. Never writes user state into the application or install root.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_ROOT: Path = Path(__file__).resolve().parents[1]
ASSETS_DIR: Path = APP_ROOT / "assets"
ICON_PATH: Path = ASSETS_DIR / "icon.ico"


def get_install_root(root: Path | None = None) -> Path:
    """Return the verified installation root directory.

    Checks the SYEONPET_INSTALL_ROOT environment variable (used by updater_switch),
    falling back to the provided root or APP_ROOT.
    """
    env_root = os.environ.get("SYEONPET_INSTALL_ROOT")
    if env_root and env_root.strip():
        try:
            cand = Path(env_root.strip())
            if cand.is_absolute():
                return cand.resolve()
        except Exception:
            pass
    return (root or APP_ROOT).resolve()


def find_libcrypto(root: Path | None = None) -> Path:
    """Locate the bundled OpenSSL libcrypto-3.dll.

    Checks the root directory followed by the Lib/site-packages directory.
    """
    base = (root or APP_ROOT).resolve()
    direct = base / "libcrypto-3.dll"
    if direct.is_file():
        return direct
    site_packages = base / "Lib" / "site-packages" / "libcrypto-3.dll"
    if site_packages.is_file():
        return site_packages
    return direct


def user_data_dir() -> Path:
    """Return %LOCALAPPDATA%\\SyeonPet, creating it safely if missing.

    Ensures user settings and runtime artifacts are never written into APP_ROOT.
    """
    local = os.environ.get("LOCALAPPDATA")
    if local and local.strip():
        base = Path(local.strip())
    else:
        base = Path.home() / "AppData" / "Local"
    data_dir = (base / "SyeonPet").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir
