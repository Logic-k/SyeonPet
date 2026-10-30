"""Hand-drawn wardrobe assets composited onto Syeon's existing frames.

The PNGs are static, locally bundled illustrations.  No image generation,
network requests, or changes to the original sprite sheets occur here.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap, QRegion


PRESETS = {
    "beret": ("별 베레모", "작은 별 장식이 달린 남색 베레모예요."),
    "sunhat": ("해바라기 모자", "노란 꽃이 달린 햇살 모자예요."),
    "ribbon_hat": ("분홍 리본 모자", "분홍 리본으로 꾸민 모자예요."),
    "scarf_blue": ("하늘 목도리", "따뜻한 하늘색 목도리예요."),
    "scarf_pink": ("분홍 목도리", "따뜻한 분홍색 목도리예요."),
    "cardigan": ("하늘 가디건", "노란 단추가 달린 하늘색 가디건이에요."),
    "apron": ("노란 앞치마", "작은 주머니가 달린 노란 앞치마예요."),
}
_HEAD = {"beret", "sunhat", "ribbon_hat"}
_ASSETS = Path(__file__).resolve().parent / "assets" / "wardrobe_presets"
_IMAGE_CACHE = {}
_POSITION_CACHE = OrderedDict()
_PRECOMPUTED = None


def _known_anchors():
    """Load the shipped anchor table once; invalid data falls back safely."""
    global _PRECOMPUTED
    if _PRECOMPUTED is not None:
        return _PRECOMPUTED
    result = {}
    try:
        path = _ASSETS / "anchors.json"
        if path.stat().st_size > 128 * 1024:
            raise ValueError("anchor table too large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("schema") != 1
                or data.get("width") != 256 or data.get("height") != 256
                or not isinstance(data.get("frames"), dict) or len(data["frames"]) > 1000):
            raise ValueError("invalid anchor table")
        for digest, value in data["frames"].items():
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)
                    or not isinstance(value, list) or len(value) != 2):
                raise ValueError("invalid anchor")
            box, face = value
            if (not isinstance(box, list) or len(box) != 4
                    or any(type(p) is not int or not 0 <= p < 256 for p in box)
                    or box[0] > box[2] or box[1] > box[3]
                    or (face is not None and (type(face) is not int or not 0 <= face < 256))):
                raise ValueError("invalid anchor")
            result[digest] = (tuple(box), face)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        result = {}
    _PRECOMPUTED = result
    return result


def _remember(key, value):
    _POSITION_CACHE[key] = value
    if len(_POSITION_CACHE) > 512:
        _POSITION_CACHE.popitem(last=False)
    return value


def _image(choice):
    if choice not in _IMAGE_CACHE:
        image = QPixmap(str(_ASSETS / (choice + ".png")))
        if not image.isNull():
            bounds = QRegion(image.mask()).boundingRect()
            image = image.copy(bounds) if not bounds.isEmpty() else QPixmap()
        _IMAGE_CACHE[choice] = image
    return _IMAGE_CACHE[choice]


def _anchors(frame):
    """Identify this frame's leaf and face without modifying source pixels.

    User-imported characters may look different; absence of a green leaf is
    deliberately a nonfatal fallback to the unmodified frame.
    """
    key = frame.cacheKey()
    if key in _POSITION_CACHE:
        _POSITION_CACHE.move_to_end(key)
        return _POSITION_CACHE[key]
    image = frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    if image.width() != 256 or image.height() != 256:
        return None
    data = bytes(image.constBits())
    # Pixel equality is exact, so a custom/imported pack cannot accidentally
    # use a different frame's anchor. Unknown frames retain dynamic detection.
    known = _known_anchors().get(hashlib.sha256(data).hexdigest())
    if known is not None:
        return _remember(key, known)
    leaf = set()
    face = set()
    for y in range(256):
        row = data[y * 1024:(y + 1) * 1024]
        for x in range(256):
            i = x * 4
            r, g, b, a = row[i:i + 4]
            if a > 150 and g > 100 and r < 140 and b < 100 and g > r * 1.3 and g > b * 1.5:
                leaf.add(y * 256 + x)
            # The cheeks are more reliable than the beak, which resembles feet.
            if a > 160 and r > 180 and b > 130 and r - g > 20 and abs(r - b) < 85:
                face.add(y * 256 + x)
    biggest = []
    while leaf:
        stack, component = [leaf.pop()], []
        while stack:
            n = stack.pop()
            component.append(n)
            x, y = n % 256, n // 256
            for adjacent in (n - 1 if x else -1, n + 1 if x < 255 else -1,
                             n - 256 if y else -1, n + 256 if y < 255 else -1):
                if adjacent in leaf:
                    leaf.remove(adjacent)
                    stack.append(adjacent)
        if len(component) > len(biggest):
            biggest = component
    if len(biggest) < 20:
        value = None
    else:
        xs, ys = [p % 256 for p in biggest], [p // 256 for p in biggest]
        leaf_box = min(xs), min(ys), max(xs), max(ys)
        left, top, right, bottom = leaf_box
        # Find actual cheeks, not small pink spots on the bag. Keep only pink
        # connected components with an area characteristic of a cheek.
        candidates = {p for p in face if left - 60 <= p % 256 <= right + 60
                      and bottom + 3 <= p // 256 <= min(255, bottom + 95)}
        components = []
        while candidates:
            stack, component = [candidates.pop()], []
            while stack:
                n = stack.pop()
                component.append(n)
                x, y = n % 256, n // 256
                for adjacent in (n - 1 if x else -1, n + 1 if x < 255 else -1,
                                 n - 256 if y else -1, n + 256 if y < 255 else -1):
                    if adjacent in candidates:
                        candidates.remove(adjacent)
                        stack.append(adjacent)
            if len(component) >= 45:
                components.append(component)
        components.sort(key=len, reverse=True)
        rows = {}
        for component in components[:2]:
            for p in component:
                y = p // 256
                rows[y] = rows.get(y, 0) + 1
        # Isolated antialiased pixels may connect the cheek to bag details;
        # the actual lower cheek spans at least several adjacent pixels.
        face_bottom = max((y for y, count in rows.items() if count >= 8), default=None)
        value = leaf_box, face_bottom
    return _remember(key, value)


def decorate_preset(frame: QPixmap, choice: str) -> QPixmap:
    """Return a dressed frame; missing/unsupported frames remain unchanged.

    Headwear follows each source frame's leaf. Bodywear is limited to pale
    body pixels below the cheeks so the face, bag and wings remain in front.
    The body may be partly or entirely hidden in reclining or obscured poses.
    """
    if choice not in PRESETS or frame.isNull():
        return frame
    try:
        anchors, asset = _anchors(frame), _image(choice)
        if anchors is None or asset.isNull():
            return frame
        (left, top, right, bottom), face_bottom = anchors
        leaf_width = right - left + 1
        if choice in _HEAD:
            width, height = min(124, round(leaf_width * 1.3)), min(90, round(leaf_width * .8))
            item = asset.scaled(max(1, width), max(1, height),
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            x = round((left + right - item.width()) / 2)
            y = round(top - item.height() * .38)
        else:
            if face_bottom is None:
                return frame
            width = min(125, max(48, round(leaf_width * 1.35)))
            height = min(70, max(24, round(leaf_width * (.55 if "scarf" in choice else .9))))
            item = asset.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            x = round((left + right - item.width()) / 2)
            y = face_bottom - (2 if "scarf" in choice else -3)
        x = max(0, min(256 - item.width(), x))
        y = max(0, min(256 - item.height(), y))
        result = frame.copy()
        painter = QPainter(result)
        painter.drawPixmap(QRect(x, y, item.width(), item.height()), item)
        painter.end()
        if choice not in _HEAD:
            original = frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            rendered = result.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            source, pixels = bytes(original.constBits()), bytearray(rendered.constBits())
            for py in range(y, y + item.height()):
                for px in range(x, x + item.width()):
                    i = py * 1024 + px * 4
                    r, g, b, a = source[i:i + 4]
                    # Preserve silhouette, dark wings/eyes, green leaf, red
                    # bag, orange beak/feet and pink cheeks. Only near-white
                    # pixels can receive body clothing.
                    body_white = a > 230 and min(r, g, b) > 205 and max(r, g, b) - min(r, g, b) < 28
                    if not body_white or py <= face_bottom:
                        pixels[i:i + 4] = source[i:i + 4]
            result = QPixmap.fromImage(QImage(bytes(pixels), 256, 256, 1024,
                                              QImage.Format.Format_RGBA8888).copy())
        return result
    except (ValueError, RuntimeError, OverflowError):
        return frame
