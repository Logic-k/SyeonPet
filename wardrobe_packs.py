"""Validate and copy locally selected Sprite-Gen atlases into the wardrobe.

This importer does not invoke image-generation services.  It accepts only the
four fixed files consumed by SpriteBank, and never edits the selected source.
Successful structural validation is not a guarantee of visual quality or rights.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import struct
import tempfile
import uuid
from pathlib import Path

from PySide6.QtGui import QImage

_FILES = ("manifest.json", "sprite-sheet-alpha.png",
          "thinking/manifest.json", "thinking/sprite-sheet-alpha.png")
_MAIN_STATES = {"idle": 8, "walk": 12, "wave": 8, "sleep": 4,
                "drag": 4, "celebrate": 9, "jump": 8, "dance": 10,
                "liesleep": 6, "still": 6}
_THINKING_STATES = {"thinking": 6}
_ONESHOT_STATES = {"wave", "celebrate", "jump"}
_MAX_MANIFEST = 2 * 1024 * 1024
_MAX_PNG = 24 * 1024 * 1024
_MAX_PIXELS = 32 * 1024 * 1024
_MAX_DIM = 8192
_REPARSE_POINT = 0x400


class WardrobePackError(ValueError):
    """The selected pack cannot be used without risking an incomplete motion."""


def _regular_file(path: Path, maximum: int) -> bytes:
    try:
        # Check the immediate folder as well; never follow a link supplied as a
        # thinking directory or one of the four selected asset files.
        for candidate in (path.parent, path):
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
                raise WardrobePackError("링크가 아닌 실제 폴더와 파일을 선택해 주세요.")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size < 1 or info.st_size > maximum:
            raise WardrobePackError("의상 파일이 없거나 크기가 허용 범위를 벗어났습니다.")
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
        if not data or len(data) > maximum:
            raise WardrobePackError("의상 파일 크기가 허용 범위를 벗어났습니다.")
        return data
    except WardrobePackError:
        raise
    except (OSError, ValueError) as exc:
        raise WardrobePackError("의상 파일을 읽을 수 없습니다. 폴더의 네 파일을 확인해 주세요.") from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WardrobePackError("매니페스트에 같은 항목이 반복됩니다.")
        result[key] = value
    return result


def _load_manifest(data: bytes):
    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise WardrobePackError("의상 매니페스트를 읽을 수 없습니다.") from exc


def _load_sheet(data: bytes) -> QImage:
    # Read PNG IHDR before Qt's decoder allocates memory.
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise WardrobePackError("투명 배경 PNG 스프라이트 시트가 필요합니다.")
    width, height = struct.unpack(">II", data[16:24])
    if not (0 < width <= _MAX_DIM and 0 < height <= _MAX_DIM and width * height <= _MAX_PIXELS):
        raise WardrobePackError("스프라이트 시트 크기가 허용 범위를 벗어났습니다.")
    image = QImage.fromData(data, "PNG")
    if image.isNull() or image.width() != width or image.height() != height or not image.hasAlphaChannel():
        raise WardrobePackError("투명 배경이 있는 올바른 PNG가 필요합니다.")
    return image


def _validate_atlas(manifest_bytes: bytes, image_bytes: bytes, expected: dict[str, int]):
    manifest = _load_manifest(manifest_bytes)
    sheet = _load_sheet(image_bytes)
    try:
        rows = manifest["frame_layout"]["rows"]
        animations = manifest["animation"]["rows"]
        if not isinstance(rows, dict) or not isinstance(animations, dict):
            raise ValueError
        if set(rows) != set(expected) or set(animations) != set(expected):
            raise ValueError
        for name, count in expected.items():
            rects, meta = rows[name], animations[name]
            if not isinstance(rects, list) or len(rects) != count or not isinstance(meta, dict):
                raise ValueError
            if (type(meta.get("frames")) is not int or meta["frames"] != count
                    or type(meta.get("loop")) is not bool
                    or meta["loop"] is not (name not in _ONESHOT_STATES)):
                raise ValueError
            durations = meta.get("durations_ms")
            if (not isinstance(durations, list) or len(durations) != count
                    or any(type(d) is not int or d <= 0 or d > 10000 for d in durations)):
                raise ValueError
            for rect in rects:
                if not isinstance(rect, dict):
                    raise ValueError
                x, y, w, h = (rect[k] for k in ("x", "y", "w", "h"))
                if (any(type(v) is not int for v in (x, y, w, h)) or x < 0 or y < 0
                        or w != 256 or h != 256 or x + w > sheet.width() or y + h > sheet.height()):
                    raise ValueError
                frame = sheet.copy(x, y, w, h).convertToFormat(QImage.Format.Format_RGBA8888)
                if frame.isNull():
                    raise ValueError
                alpha = bytes(frame.constBits())[3::4]
                if not any(alpha) or not any(a < 255 for a in alpha):
                    raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise WardrobePackError("동작 프레임, 투명 배경 또는 재생 정보가 올바르지 않습니다.") from exc


def _read_and_validate(folder: Path) -> dict[str, bytes]:
    folder = Path(folder)
    try:
        info = folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
            raise WardrobePackError("링크가 아닌 실제 의상 폴더를 선택해 주세요.")
        files = {name: _regular_file(folder / name, _MAX_PNG if name.endswith(".png") else _MAX_MANIFEST)
                 for name in _FILES}
        _validate_atlas(files[_FILES[0]], files[_FILES[1]], _MAIN_STATES)
        _validate_atlas(files[_FILES[2]], files[_FILES[3]], _THINKING_STATES)
        return files
    except WardrobePackError:
        raise
    except (OSError, ValueError) as exc:
        raise WardrobePackError("의상 폴더를 읽을 수 없습니다.") from exc


def validate_sprite_pack(folder: Path) -> None:
    """Raise WardrobePackError unless both atlases contain every required state."""
    _read_and_validate(folder)


def import_sprite_pack(source_folder: Path, user_packs_folder: Path) -> Path:
    """Copy only validated bytes into a unique new directory; preserve source."""
    files = _read_and_validate(source_folder)
    destination = Path(user_packs_folder)
    staging = None
    try:
        destination.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink() or getattr(destination.lstat(), "st_file_attributes", 0) & _REPARSE_POINT:
            raise WardrobePackError("의상 저장 폴더를 확인해 주세요.")
        staging = Path(tempfile.mkdtemp(prefix=".import-", dir=destination))
        (staging / "thinking").mkdir()
        for name, data in files.items():
            with (staging / name).open("xb") as stream:
                stream.write(data)
        # Re-read the exact saved files before publishing the new pack.
        _read_and_validate(staging)
        target = destination / ("pack-" + uuid.uuid4().hex)
        if target.exists():
            raise WardrobePackError("의상 저장 이름이 중복되었습니다. 다시 시도해 주세요.")
        os.rename(staging, target)
        staging = None
        return target
    except WardrobePackError:
        raise
    except OSError as exc:
        raise WardrobePackError("의상을 저장할 수 없습니다. 저장 공간과 권한을 확인해 주세요.") from exc
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
