"""Build a reproducible Windows portable ZIP without user's configuration.

The build is an allowlist, not a copy of the working directory. Do not publish
the result until application functionality, redistribution rights and release
contents are independently verified. Requires Python 3.10+ and no packages.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_FILES = (
    "behavior.py", "bubble.py", "chat.py", "main.py", "onboarding.py",
    "optimizer_ai.py", "optimizer_chat.py", "optimizer_core.py",
    "optimizer_library.py", "optimizer_templates.py", "pet.py",
    "version.py", "update_checker.py", "update_trust.py", "updater_core.py",
    "updater_switch.py", "updater_ui.py", "wardrobe.py", "wardrobe_packs.py",
)
ROOT_RUNTIME = (
    "SyeonPet.exe", "pythonw.exe", "python3.dll", "python312.dll",
    "python312.zip", "python312._pth", "vcruntime140.dll",
    "vcruntime140_1.dll", "libcrypto-3.dll", "libssl-3.dll", "libffi-8.dll",
    "_asyncio.pyd", "_bz2.pyd", "_ctypes.pyd", "_decimal.pyd",
    "_elementtree.pyd", "_hashlib.pyd", "_lzma.pyd", "_msi.pyd",
    "_multiprocessing.pyd", "_overlapped.pyd", "_queue.pyd", "_socket.pyd",
    "_ssl.pyd", "_uuid.pyd", "_wmi.pyd", "_zoneinfo.pyd",
    "pyexpat.pyd", "select.pyd", "unicodedata.pyd", "winsound.pyd",
)
ASSETS = (
    "assets/base-source.png", "assets/icon.ico", "assets/manifest.json",
    "assets/sprite-sheet-alpha.png", "assets/prompt_templates.json",
    "assets/seobu_office_templates.json", "assets/thinking/manifest.json",
    "assets/thinking/sprite-sheet-alpha.png",
)
# Only these public guides are bundled; other docs may contain private handoffs.
GUIDES = (
    "docs/유초등교육과_션_사용가이드.html",
    "docs/유초등교육과_션_사용가이드.pdf",
    "docs/SPRITE_WARDROBE.md",
)
DIST_INFOS = (
    "pyside6-6.11.2.dist-info", "pyside6_essentials-6.11.2.dist-info",
    "shiboken6-6.11.2.dist-info",
)
QT_RUNTIME = (
    "PySide6_Addons.json", "PySide6_Essentials.json", "Qt6Core.dll", "Qt6Gui.dll",
    "Qt6Widgets.dll", "QtCore.pyd", "QtGui.pyd", "QtWidgets.pyd", "__init__.py",
    "_config.py", "_git_pyside_version.py", "concrt140.dll", "msvcp140.dll",
    "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_codecvt_ids.dll",
    "plugins/iconengines/qsvgicon.dll", "plugins/imageformats/qgif.dll",
    "plugins/imageformats/qicns.dll", "plugins/imageformats/qico.dll",
    "plugins/imageformats/qjpeg.dll", "plugins/imageformats/qpdf.dll",
    "plugins/imageformats/qsvg.dll", "plugins/imageformats/qtga.dll",
    "plugins/imageformats/qtiff.dll", "plugins/imageformats/qwbmp.dll",
    "plugins/imageformats/qwebp.dll", "plugins/platforms/qdirect2d.dll",
    "plugins/platforms/qminimal.dll", "plugins/platforms/qoffscreen.dll",
    "plugins/platforms/qwindows.dll", "plugins/styles/qmodernwindowsstyle.dll",
    "pyside6.abi3.dll", "qt6core_metatypes.json", "qt6gui_metatypes.json",
    "qt6widgets_metatypes.json", "vcamp140.dll", "vccorlib140.dll",
    "vcomp140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
)
SHIBOKEN_RUNTIME = (
    "Shiboken.pyd", "__init__.py", "_config.py", "_git_shiboken_module_version.py",
    "concrt140.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "msvcp140_codecvt_ids.dll", "shiboken6.abi3.dll", "vcamp140.dll",
    "vccorlib140.dll", "vcomp140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
)
LICENSES = ("PYTHON-3.12.7.txt", "LGPL-3.0-only.txt", "GPL-2.0-only.txt", "GPL-3.0-only.txt")
README = """션 데스크톱 프롬프트 도우미 (Windows)

SyeonPet.exe를 실행하세요. run.cmd는 동일한 소스를 실행하는 대체 방법입니다.
설정 config.json은 새 배포물의 공개 기본값입니다. 기존 앱의 개인 설정을 자동으로
덮어쓰거나 이전하지 않습니다. 보관함은 사용자 로컬 앱 데이터에 별도로 저장됩니다.
외부 AI로 보내기 전에는 기관의 개인정보 지침과 허용 서비스를 확인하세요.
처음 선택된 Vireonix는 사용자가 전송을 확인할 때만 요청을 보냅니다.
기본 정리 (AI 사용 안 함)를 선택하면 외부 AI 호출 없이 입력을 정리합니다.
션을 우클릭하여 션이 옷장을 열고 장식이나 검증한 외형 팩을 선택할 수 있습니다.
스프라이트 팩 형식은 docs/SPRITE_WARDROBE.md를 참고하세요.
새 업데이트는 서명 확인 후 준비되며 션 메뉴에서 종료하고 다시 실행할 때 적용됩니다.
이전 v0.1.5 이하에서는 자동 업데이트를 사용할 수 없어 이 버전을 한 번 직접 설치해야 합니다.
사용 방법은 docs/유초등교육과_션_사용가이드.html 또는
docs/유초등교육과_션_사용가이드.pdf에서 확인하세요. 자세한 제3자 고지는 licenses를 보세요.
"""


def _source_version_text(text: str) -> str:
    tree = ast.parse(text)
    for item in tree.body:
        if isinstance(item, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in item.targets):
            value = ast.literal_eval(item.value)
            if isinstance(value, str):
                return value
    raise ValueError("version.py에 문자열 APP_VERSION이 없습니다.")


def _source_version(root: Path) -> str:
    return _source_version_text((root / "version.py").read_text(encoding="utf-8"))


def _file(root: Path, name: str) -> Path:
    source = root / name
    # Reject path traversal and symlinked files/directories, not just final links.
    if Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError(f"잘못된 패키지 경로: {name}")
    for parent in (source, *source.parents):
        if parent == root.parent:
            break
        if parent.is_symlink():
            raise ValueError(f"심볼릭 링크는 포함할 수 없습니다: {name}")
    if not source.is_file() or not source.resolve().is_relative_to(root.resolve()):
        raise FileNotFoundError(f"필수 파일을 찾을 수 없습니다: {name}")
    return source


def _check_app_imports(sources: dict[str, str], local_modules: set[str]) -> None:
    allowed = set(APP_FILES)
    for name, source in sources.items():
        tree = ast.parse(source)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [item.name.split(".")[0] for item in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for module in names:
                local = module + ".py"
                if local in local_modules and local not in allowed:
                    raise ValueError(f"배포 목록에 로컬 모듈을 추가해야 합니다: {name}: {local}")


def collect_files(root: Path) -> dict[str, Path]:
    root = root.resolve()
    result = {name: _file(root, name) for name in (*APP_FILES, *ROOT_RUNTIME, *ASSETS, *GUIDES, "run.cmd")}
    result["config.json"] = _file(root, "tools/default-config.json")
    result["licenses/THIRD_PARTY.md"] = _file(root, "tools/THIRD_PARTY.md")
    for name in LICENSES:
        result["licenses/" + name] = _file(root, "tools/licenses/" + name)
    for package, names in (("PySide6", QT_RUNTIME), ("shiboken6", SHIBOKEN_RUNTIME)):
        for item in names:
            name = f"Lib/site-packages/{package}/{item}"
            result[name] = _file(root, name)
    for dist in DIST_INFOS:
        name = f"Lib/site-packages/{dist}/METADATA"
        result[name] = _file(root, name)
        name = f"Lib/site-packages/{dist}/licenses/LicenseRef-Qt-Commercial.txt"
        result[name] = _file(root, name)
    return result


def _zip_entry(name: str, data: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info, data


def _revision_blobs(root: Path, revision: str, files: dict[str, Path]) -> dict[str, str]:
    """Get committed blob identities, including the code producing README."""
    paths = sorted({path.relative_to(root).as_posix() for path in files.values()} |
                   {"tools/build_release.py"})
    try:
        head = subprocess.run(["git", "rev-parse", "--verify", revision + "^{commit}"],
                              cwd=root, check=True, capture_output=True, text=True).stdout.strip()
        raw = subprocess.run(["git", "ls-tree", "-r", "-z", "--full-tree", revision, "--", *paths],
                             cwd=root, check=True, capture_output=True).stdout.decode("utf-8")
        blobs = {}
        for record in raw.split("\0"):
            if record:
                meta, name = record.split("\t", 1)
                mode, kind, oid = meta.split(" ", 2)
                if kind != "blob" or mode not in ("100644", "100755"):
                    raise ValueError("배포 커밋에 일반 파일이 아닌 항목이 있습니다.")
                blobs[name] = oid
        if head.lower() != revision.lower() or set(paths) - set(blobs):
            raise ValueError("배포 파일 중 커밋에 추적되지 않은 파일이 있습니다.")
        return blobs
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ValueError("배포 커밋을 검증하지 못했습니다.") from exc


def _git_blob(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def _committed_bytes(root: Path, blobs: dict[str, str]) -> dict[str, bytes]:
    """Read exact Git blobs in one bounded batch; never package worktree data."""
    identifiers = list(dict.fromkeys(blobs.values()))
    try:
        result = subprocess.run(["git", "cat-file", "--batch"], cwd=root,
                                input=("\n".join(identifiers) + "\n").encode("ascii"),
                                check=True, capture_output=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("커밋의 파일 내용을 읽지 못했습니다.") from exc
    buffer = result.stdout
    content = {}
    offset = 0
    for expected in identifiers:
        end = buffer.find(b"\n", offset)
        if end < 0:
            raise ValueError("커밋의 파일 응답이 잘렸습니다.")
        header = buffer[offset:end].decode("ascii").split()
        if len(header) != 3 or header[0] != expected or header[1] != "blob":
            raise ValueError("커밋의 파일 응답이 올바르지 않습니다.")
        size = int(header[2])
        if size < 0 or size > 100_000_000:
            raise ValueError("커밋의 파일 크기가 허용 범위를 벗어났습니다.")
        start, stop = end + 1, end + 1 + size
        data = buffer[start:stop]
        if len(data) != size or buffer[stop:stop + 1] != b"\n" or _git_blob(data) != expected:
            raise ValueError("커밋의 파일 무결성 검사가 실패했습니다.")
        content[expected] = data
        offset = stop + 1
    if offset != len(buffer):
        raise ValueError("커밋 파일 응답에 예상하지 못한 데이터가 있습니다.")
    return content


def _revision_local_modules(root: Path, revision: str) -> set[str]:
    """Inspect the same commit's top-level names, not the working tree."""
    try:
        raw = subprocess.run(["git", "ls-tree", "--name-only", "-z", revision],
                             cwd=root, check=True, capture_output=True).stdout.decode("utf-8")
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ValueError("커밋의 로컬 모듈 목록을 읽지 못했습니다.") from exc
    return {name for name in raw.split("\0") if name.endswith(".py") and "/" not in name}


def _builder_git_hash(root: Path) -> str:
    """Use Git's configured clean filters for the running worktree builder."""
    try:
        return subprocess.run(["git", "hash-object", "--path=tools/build_release.py",
                               "--", "tools/build_release.py"], cwd=root,
                              check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("패키지 빌더를 검증하지 못했습니다.") from exc


def build(root: Path, output: Path, version: str, revision: str | None = None) -> dict:
    root = root.resolve()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("버전은 0.1.0 또는 0.1.0-rc.1 형식이어야 합니다.")
    if not revision and _source_version(root) != version:
        raise ValueError("배포 버전과 version.py의 APP_VERSION이 일치하지 않습니다.")
    if revision is not None and not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise ValueError("소스 커밋은 40자리 SHA여야 합니다.")
    output = output.resolve()
    if output.suffix.lower() != ".zip" or output.is_relative_to(root):
        raise ValueError("ZIP 출력은 저장소 바깥의 .zip 경로여야 합니다.")
    if not output.parent.is_dir():
        raise FileNotFoundError("출력 폴더를 먼저 만들어 주세요.")
    checksum = Path(str(output) + ".sha256")
    if output.exists() or checksum.exists():
        raise FileExistsError("같은 이름의 배포 파일이 이미 있습니다. 새 경로를 지정하세요.")
    files = collect_files(root)
    blobs = _revision_blobs(root, revision, files) if revision else None
    if blobs and _builder_git_hash(root) != blobs["tools/build_release.py"]:
        raise ValueError("패키지 빌더가 지정한 커밋과 다릅니다.")
    committed = _committed_bytes(root, blobs) if blobs else None
    if committed is not None:
        sources = {name: committed[blobs[name]].decode("utf-8") for name in APP_FILES}
        _check_app_imports(sources, _revision_local_modules(root, revision))
    else:
        sources = {name: _file(root, name).read_text(encoding="utf-8") for name in APP_FILES}
        _check_app_imports(sources, {path.name for path in root.glob("*.py") if path.is_file()})
    manifest = {"version": version, "source_commit": revision, "files": {}}
    created_zip = False
    created_checksum = False
    try:
        with output.open("xb") as raw:
            created_zip = True
            with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for name, source in sorted(files.items()):
                    source_name = source.relative_to(root).as_posix()
                    data = committed[blobs[source_name]] if committed is not None else source.read_bytes()
                    if name == "version.py" and _source_version_text(data.decode("utf-8")) != version:
                        raise ValueError("압축 중 버전 파일이 변경되었습니다.")
                    manifest["files"][name] = hashlib.sha256(data).hexdigest()
                    archive.writestr(*_zip_entry(name, data))
                data = README.encode("utf-8")
                manifest["files"]["사용안내.txt"] = hashlib.sha256(data).hexdigest()
                archive.writestr(*_zip_entry("사용안내.txt", data))
                archive.writestr(*_zip_entry("package-manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")))
        if blobs and _builder_git_hash(root) != blobs["tools/build_release.py"]:
            raise ValueError("압축 중 패키지 빌더가 변경되었습니다.")
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        with checksum.open("x", encoding="utf-8", newline="\n") as check:
            created_checksum = True
            check.write(f"{digest}  {output.name}\n")
    except BaseException:
        # Remove only the new files created by this invocation.
        if created_checksum:
            checksum.unlink(missing_ok=True)
        if created_zip:
            output.unlink(missing_ok=True)
        raise
    return {"output": str(output), "sha256": digest, "file_count": len(manifest["files"]) + 1}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--revision", help="Optional verified 40-character commit SHA")
    args = parser.parse_args()
    try:
        result = build(ROOT, args.output, args.version, args.revision)
    except (ValueError, OSError, SyntaxError) as exc:
        print(f"릴리스 패키지 실패: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
