"""Generate bold, cute wardrobe item PNGs with Qt painting (no external deps).

Run with the embedded runtime:  pythonw tools/gen_bold_costumes.py
Each item is drawn on a transparent 256x160 canvas (head items anchor to the
leaf box; body items like capes use a 256x256 canvas like sailor/cape_cozy).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Lib" / "site-packages"))

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QImage, QPainter, QPainterPath,
                           QPen, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QApplication

OUT = ROOT / "assets" / "wardrobe_presets"
OUTLINE = QColor(60, 40, 55)


def canvas(w=256, h=160):
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return img, p


def poly(p, points, fill, pen_w=4):
    p.setPen(QPen(OUTLINE, pen_w, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QBrush(fill))
    p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))


def ellipse(p, cx, cy, rx, ry, fill, pen_w=4, pen_color=None):
    p.setPen(QPen(pen_color or OUTLINE, pen_w, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QBrush(fill))
    p.drawEllipse(QPointF(cx, cy), rx, ry)


def save(img, name):
    path = OUT / f"{name}.png"
    img.save(str(path))
    print("wrote", path.name)


def cat_ears():
    img, p = canvas()
    # headband arc
    p.setPen(QPen(QColor(45, 45, 60), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(60, 78, 136, 120), 15 * 16, 150 * 16)
    # ears: dark triangles with pink inner
    for cx, flip in ((92, -1), (164, 1)):
        poly(p, [(cx - 34 * 1, 110), (cx + 34, 110), (cx + 10 * flip - 10 * flip, 26)],
             QColor(45, 45, 60))
        poly(p, [(cx - 18, 100), (cx + 18, 100), (cx, 52)], QColor(255, 170, 200), 3)
    p.end()
    save(img, "cat_ears")


def party_hat():
    img, p = canvas()
    # cone
    poly(p, [(128 - 52, 132), (128 + 52, 132), (128, 18)], QColor(255, 95, 140))
    # diagonal stripes clipped to cone
    path = QPainterPath()
    path.moveTo(76, 132); path.lineTo(180, 132); path.lineTo(128, 18); path.closeSubpath()
    p.setClipPath(path)
    for y0 in (60, 90, 118):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 210, 80) if y0 % 2 else QColor(120, 220, 190))
        p.drawRect(QRectF(60, y0, 140, 14))
    p.setClipping(False)
    # redraw outline on top
    p.setPen(QPen(OUTLINE, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    # pom-pom
    ellipse(p, 128, 16, 15, 15, QColor(255, 230, 90))
    p.end()
    save(img, "party_hat")


def big_ribbon():
    img, p = canvas()
    pink = QColor(255, 85, 145)
    # loops
    for cx, pts in ((84, [(128, 88), (30, 46), (30, 120)]),
                    (172, [(128, 88), (226, 46), (226, 120)])):
        poly(p, pts, pink)
    # tails
    poly(p, [(112, 104), (96, 150), (122, 148), (126, 116)], pink)
    poly(p, [(144, 104), (160, 150), (134, 148), (130, 116)], pink)
    # dots on loops
    for cx, cy in ((62, 70), (76, 100), (96, 82), (194, 70), (180, 100), (160, 82)):
        ellipse(p, cx, cy, 7, 7, QColor(255, 235, 245), 0)
    # center knot
    ellipse(p, 128, 92, 17, 15, QColor(255, 60, 120))
    p.end()
    save(img, "big_ribbon")


def crown():
    img, p = canvas()
    gold = QColor(255, 205, 60)
    pts = [(74, 132), (74, 66), (98, 98), (128, 56), (158, 98), (182, 66), (182, 132)]
    poly(p, pts, gold)
    # band
    p.setPen(QPen(OUTLINE, 4))
    p.setBrush(QColor(230, 170, 40))
    p.drawRect(QRectF(74, 116, 108, 16))
    # jewels
    for cx, c in ((96, QColor(235, 70, 90)), (128, QColor(90, 170, 255)), (160, QColor(120, 230, 130))):
        ellipse(p, cx, 122, 7, 7, c, 2)
    p.end()
    save(img, "crown")


def strawberry():
    img, p = canvas()
    red = QColor(235, 60, 75)
    # body: dome narrowing to bottom point
    path = QPainterPath()
    path.moveTo(56, 96)
    path.cubicTo(56, 40, 96, 26, 128, 26)
    path.cubicTo(160, 26, 200, 40, 200, 96)
    path.cubicTo(200, 122, 160, 146, 128, 150)
    path.cubicTo(96, 146, 56, 122, 56, 96)
    p.setPen(QPen(OUTLINE, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QBrush(red))
    p.drawPath(path)
    # seeds
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 235, 200))
    for cx, cy in ((82, 78), (104, 62), (128, 56), (152, 62), (174, 78),
                   (94, 104), (128, 98), (162, 104), (114, 128), (142, 128), (128, 138)):
        p.drawEllipse(QPointF(cx, cy), 4.2, 6)
    # green calyx on top
    poly(p, [(86, 34), (104, 18), (112, 32)], QColor(80, 190, 90))
    poly(p, [(112, 32), (128, 8), (144, 32)], QColor(80, 190, 90))
    poly(p, [(144, 32), (152, 18), (170, 34)], QColor(80, 190, 90))
    p.end()
    save(img, "strawberry")


def hero_cape():
    img, p = canvas(256, 256)
    red = QColor(215, 45, 60)
    # cape body flowing down from shoulders
    path = QPainterPath()
    path.moveTo(58, 60)
    path.cubicTo(90, 44, 166, 44, 198, 60)
    path.cubicTo(210, 130, 214, 190, 206, 232)
    path.cubicTo(170, 246, 86, 246, 50, 232)
    path.cubicTo(42, 190, 46, 130, 58, 60)
    p.setPen(QPen(OUTLINE, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    grad = QRadialGradient(QPointF(128, 140), 130)
    grad.setColorAt(0, QColor(235, 70, 85))
    grad.setColorAt(1, red)
    p.setBrush(QBrush(grad))
    p.drawPath(path)
    # collar
    poly(p, [(58, 60), (90, 44), (128, 52), (166, 44), (198, 60), (198, 74), (58, 74)],
         QColor(255, 220, 80))
    # star emblem
    cx, cy, r_out, r_in = 128, 150, 34, 14
    star = []
    import math as _m
    for i in range(10):
        r = r_out if i % 2 == 0 else r_in
        a = -_m.pi / 2 + i * _m.pi / 5
        star.append((cx + r * _m.cos(a), cy + r * _m.sin(a)))
    poly(p, star, QColor(255, 225, 90))
    p.end()
    save(img, "hero_cape")


def main():
    app = QApplication.instance() or QApplication([])
    cat_ears(); party_hat(); big_ribbon(); crown(); strawberry(); hero_cape()


if __name__ == "__main__":
    main()
