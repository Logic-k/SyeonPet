"""CLI and helper tool for Windows Desktop and Start Menu shortcut creation.

Re-exports core shortcut helpers from syeonpet.ui.shortcut_helper and provides a
standalone command-line interface.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Ensure repository root is on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syeonpet.ui.shortcut_helper import (
    SHORTCUT_DESC,
    SHORTCUT_NAME,
    create_desktop_shortcut,
    create_shortcut,
    create_start_menu_shortcut,
    get_desktop_dir,
    get_start_menu_dir,
    resolve_launcher_target,
)

__all__ = [
    "SHORTCUT_DESC",
    "SHORTCUT_NAME",
    "create_desktop_shortcut",
    "create_shortcut",
    "create_start_menu_shortcut",
    "get_desktop_dir",
    "get_start_menu_dir",
    "resolve_launcher_target",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="션 데스크탑 펫 바로가기 생성 도구")
    parser.add_argument("--desktop", action="store_true", help="바탕화면 바로가기 생성")
    parser.add_argument("--start-menu", action="store_true", help="시작 메뉴 바로가기 생성")
    parser.add_argument("--target-dir", type=Path, default=None, help="바로가기를 저장할 대상 디렉터리")
    parser.add_argument("--install-root", type=Path, default=None, help="설치 디렉터리 경로")
    parser.add_argument("--dry-run", action="store_true", help="실제 파일 생성 없이 대상 경로만 확인")
    args = parser.parse_args()

    # Default to desktop shortcut if neither is explicitly selected
    create_desktop = args.desktop or (not args.desktop and not args.start_menu)
    create_start = args.start_menu

    created: list[Path] = []
    if create_desktop:
        path = create_desktop_shortcut(
            install_root=args.install_root,
            target_dir=args.target_dir,
            dry_run=args.dry_run,
        )
        created.append(path)
        action = "[확인 (dry-run)]" if args.dry_run else "[생성 완료]"
        print(f"{action} 바탕화면 바로가기: {path}")

    if create_start:
        path = create_start_menu_shortcut(
            install_root=args.install_root,
            target_dir=args.target_dir,
            dry_run=args.dry_run,
        )
        created.append(path)
        action = "[확인 (dry-run)]" if args.dry_run else "[생성 완료]"
        print(f"{action} 시작 메뉴 바로가기: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
