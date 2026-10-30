"""Offline hat art and fitting for the curated wardrobe atlases.

Hats are drawn like Syeon's own leaf: flat fill, a darker outline of the same
hue and one soft highlight, instead of a dark brown sticker outline. While a
hat is worn the leaf is hidden, so the hat sits on the head crown rather than
perching on the leaf. Head roll comes from the line through both cheeks; the
leaf shape is slanted by design and would tilt the hat in upright poses.

Used only by build.py at development time; nothing here ships in the app.
"""
import math

import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

SS = 4  # supersampling for hat art
LINE = 5.0  # art units on a 256-wide canvas; about 2px once fitted to the head
HEAD_WHITE = (254, 254, 253, 255)
HATS = {
    "beret": dict(fill="#7d97cf", shade="#6680bb", ink="#3f5a95", light="#a9bde4"),
    "sunhat": dict(fill="#fbe3a0", ink="#bf9241", light="#fff3cc", band="#f0a8b3", band_ink="#c7707f"),
    "ribbon_hat": dict(fill="#f9cdd9", shade="#f0b3c5", ink="#c97893", light="#fde6ec", bow="#ef8fab"),
}


def _path(points):
    p = QPainterPath(QPointF(*(v * SS for v in points[0])))
    for kind, c in points[1:]:
        c = [v * SS for v in c]
        if kind == "C":
            p.cubicTo(*c)
        elif kind == "L":
            p.lineTo(*c)
        elif kind == "Z":
            p.closeSubpath()
    return p


def _pen(color, width):
    return QPen(QColor(color), width * SS, Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def draw_hat(name):
    """Return the hat as a tightly cropped RGBA PIL image (requires a QApplication)."""
    c = HATS[name]
    img = QImage(256 * SS, 144 * SS, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing)
    if name == "beret":
        p.setPen(_pen(c["ink"], LINE)); p.setBrush(QColor(c["fill"]))
        p.drawPath(_path([(28, 92), ("C", (34, 48, 70, 30, 120, 34)), ("C", (150, 18, 186, 26, 202, 46)),
                          ("C", (226, 60, 230, 80, 228, 96)), ("C", (190, 110, 70, 112, 28, 100)),
                          ("C", (24, 97, 24, 94, 28, 92)), ("Z", ())]))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(c["shade"]))
        p.drawPath(_path([(36, 96), ("C", (80, 104, 175, 104, 222, 94)), ("C", (222, 99, 220, 102, 216, 103)),
                          ("C", (170, 110, 80, 110, 36, 101)), ("Z", ())]))
        p.setPen(_pen(c["light"], 4)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(_path([(58, 66), ("C", (74, 50, 98, 44, 120, 44))]))
        p.setPen(_pen(c["ink"], 4)); p.drawPath(_path([(36, 96), ("C", (80, 104, 175, 104, 222, 94))]))
        p.setPen(_pen(c["ink"], LINE)); p.setBrush(QColor(c["fill"]))
        p.drawEllipse(QRectF(118 * SS, 22 * SS, 16 * SS, 14 * SS))
        pts = [(176 + (12 if i % 2 == 0 else 5.5) * math.cos(-math.pi / 2 + i * math.pi / 5),
                72 + (12 if i % 2 == 0 else 5.5) * math.sin(-math.pi / 2 + i * math.pi / 5)) for i in range(10)]
        star = QPainterPath(QPointF(pts[0][0] * SS, pts[0][1] * SS))
        for x, y in pts[1:]:
            star.lineTo(x * SS, y * SS)
        star.closeSubpath()
        p.setPen(_pen("#c39a3c", 3)); p.setBrush(QColor("#ffe58f")); p.drawPath(star)
    elif name == "sunhat":
        p.setPen(_pen(c["ink"], LINE)); p.setBrush(QColor(c["fill"]))
        p.drawPath(_path([(14, 96), ("C", (40, 80, 80, 80, 128, 82)), ("C", (178, 80, 218, 80, 242, 96)),
                          ("C", (232, 110, 186, 116, 128, 116)), ("C", (70, 116, 24, 110, 14, 96)), ("Z", ())]))
        p.drawPath(_path([(56, 88), ("C", (54, 44, 80, 28, 128, 28)), ("C", (176, 28, 202, 44, 200, 88)),
                          ("C", (170, 96, 86, 96, 56, 88)), ("Z", ())]))
        p.setPen(_pen(c["band_ink"], 3.5)); p.setBrush(QColor(c["band"]))
        p.drawPath(_path([(57, 74), ("C", (88, 82, 168, 82, 199, 74)), ("L", (200, 86)),
                          ("C", (168, 94, 88, 94, 56, 86)), ("Z", ())]))
        p.setPen(_pen(c["light"], 4)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(_path([(74, 58), ("C", (84, 44, 104, 38, 124, 38))]))
        p.setPen(_pen("#c58a2e", 2.5))
        for dx, dy in [(0, -8), (7.5, -2.5), (5, 6.5), (-5, 6.5), (-7.5, -2.5)]:
            p.setBrush(QColor("#ffd45c"))
            p.drawEllipse(QRectF((180 + dx - 5.5) * SS, (68 + dy - 5.5) * SS, 11 * SS, 11 * SS))
        p.setBrush(QColor("#d99b3f")); p.drawEllipse(QRectF(175 * SS, 63 * SS, 10 * SS, 10 * SS))
    else:  # ribbon_hat: a small cloche with a bow
        p.setPen(_pen(c["ink"], LINE)); p.setBrush(QColor(c["fill"]))
        p.drawPath(_path([(40, 92), ("C", (40, 50, 72, 30, 128, 30)), ("C", (184, 30, 214, 50, 214, 92)),
                          ("C", (186, 100, 70, 100, 40, 92)), ("Z", ())]))
        p.setBrush(QColor(c["shade"]))
        p.drawPath(_path([(30, 92), ("C", (74, 102, 182, 102, 224, 92)), ("C", (230, 96, 230, 102, 224, 106)),
                          ("C", (182, 114, 74, 114, 30, 106)), ("C", (24, 102, 24, 96, 30, 92)), ("Z", ())]))
        p.setPen(_pen(c["light"], 4)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(_path([(62, 62), ("C", (74, 46, 98, 40, 122, 40))]))
        p.setPen(_pen(c["ink"], 3.5)); p.setBrush(QColor(c["bow"]))
        p.drawPath(_path([(170, 66), ("C", (150, 46, 140, 56, 148, 72)), ("C", (154, 80, 164, 78, 170, 72)), ("Z", ())]))
        p.drawPath(_path([(178, 66), ("C", (198, 46, 208, 56, 200, 72)), ("C", (194, 80, 184, 78, 178, 72)), ("Z", ())]))
        p.setBrush(QColor("#fff2f5")); p.drawEllipse(QRectF(167 * SS, 62 * SS, 14 * SS, 13 * SS))
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    arr = np.frombuffer(bytes(img.constBits()), np.uint8).reshape(img.height(), img.width(), 4)
    art = Image.fromarray(arr.copy(), "RGBA").resize((256, 144), Image.Resampling.LANCZOS)
    return art.crop(art.getbbox())


def _leaf_mask(a, bbox):
    """Leaf pixels near the landmark box, grown 3px to include its light matting halo."""
    rgb = a[:, :, :3].astype(int)
    m = (a[:, :, 3] > 25) & (rgb[:, :, 1] > rgb[:, :, 0] + 18) & (rgb[:, :, 1] > rgb[:, :, 2] + 18)
    left, top, right, bottom = bbox
    box = np.zeros_like(m)
    box[max(0, top - 6):bottom + 7, max(0, left - 6):right + 7] = True
    m &= box
    for _ in range(3):
        grown = m.copy()
        grown[1:] |= m[:-1]; grown[:-1] |= m[1:]; grown[:, 1:] |= m[:, :-1]; grown[:, :-1] |= m[:, 1:]
        m = grown
    return m


def _remove_leaf(a, leaf):
    """Leaf pixels between head pixels on both sides become head white; others clear."""
    out = a.copy()
    solid = (a[:, :, 3] > 200) & ~leaf
    for y in np.where(leaf.any(axis=1))[0]:
        row = np.where(solid[y])[0]
        for x in np.where(leaf[y])[0]:
            inside = ((row < x) & (row > x - 70)).any() and ((row > x) & (row < x + 70)).any()
            out[y, x] = HEAD_WHITE if inside else (0, 0, 0, 0)
    return out


def head_tilt(frames, idx):
    """Head roll in degrees for one frame of a pose's landmark list."""
    md = frames[idx]
    axes = [f["cheek_axis_degrees"] for f in frames if f.get("cheek_axis_degrees") is not None]
    if len(md["cheeks"]) == 2 and md.get("cheek_axis_degrees") is not None:
        value = md["cheek_axis_degrees"]
    elif axes and len(md["cheeks"]) > 2:  # lying poses detect extra pink areas
        value = float(np.median(axes))
    else:  # side view: one cheek, upright head
        value = 0.0
    return max(-25.0, min(25.0, float(value)))


def _head_run(alpha, y, cx):
    row = alpha[y] > 100
    cx = int(round(cx))
    if not 0 <= cx < 256 or not row[cx]:
        return None
    left = right = cx
    while left > 0 and row[left - 1]:
        left -= 1
    while right < 255 and row[right + 1]:
        right += 1
    return left, right


def wear_hat(frame, landmarks, angle, art):
    """Return a new RGBA frame with the leaf hidden and the hat seated on the crown."""
    a = np.asarray(frame.convert("RGBA")).copy()
    leaf = _leaf_mask(a, landmarks["leaf"]["bbox"])
    ys, xs = np.where(leaf)
    centre = (float(xs.mean()), float(ys.mean()))
    # Measure in an upright copy so the brim follows the head, not the image rows.
    up_alpha = np.asarray(Image.fromarray(a[:, :, 3]).rotate(angle, resample=Image.Resampling.BILINEAR, center=centre))
    up_leaf = np.asarray(Image.fromarray(leaf.astype(np.uint8) * 255).rotate(angle, center=centre)) > 127
    lys, lxs = np.where(up_leaf)
    leaf_w = lxs.max() - lxs.min() + 1
    brim_y = min(255, int(np.percentile(lys, 97)) + 2)
    cx = float(np.median(lxs))
    run = _head_run(up_alpha, brim_y, cx)
    width = leaf_w * 1.25 if run is None else run[1] - run[0] + 1
    if run is not None:
        cx = (run[0] + run[1]) / 2
    width = max(leaf_w * 1.1, min(width, leaf_w * 1.5)) * 1.06
    hat = art.resize((max(1, round(width)), max(1, round(art.height * width / art.width))), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", (256, 256))
    layer.alpha_composite(hat, (round(cx - hat.width / 2), max(0, round(brim_y - hat.height * 0.9))))
    layer = layer.rotate(-angle, resample=Image.Resampling.BICUBIC, center=centre)
    out = Image.fromarray(_remove_leaf(a, leaf))
    out.alpha_composite(layer)
    return out
