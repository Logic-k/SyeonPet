"""Local, reversible accessories for Syeon's original sprite frames."""
from __future__ import annotations

import re
import struct
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QPointF, QSettings, QSignalBlocker, QStandardPaths, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QHBoxLayout,
                               QLabel, QPushButton, QRadioButton, QScrollArea,
                               QVBoxLayout, QWidget)

from wardrobe_presets import PRESETS, decorate_preset


CHOICES = {
    "original": ("기본 모습", "원래 션의 모습이에요."),
    "bow": ("하늘 리본", "산뜻한 하늘색 리본을 달아요."),
    "star": ("반짝 별", "작은 금빛 별 머리핀을 달아요."),
    "cap": ("학사모", "션과 함께 배우는 날의 작은 모자예요."),
    **PRESETS,
}
_HAIR_CACHE = OrderedDict()


def _settings(settings=None):
    return settings if settings is not None else QSettings("SyeonPet", "Wardrobe")


def read_wardrobe_choice(settings=None) -> str:
    value = _settings(settings).value("accessory", "original", type=str)
    return value if value in CHOICES else "original"


def write_wardrobe_choice(choice: str, settings=None) -> str:
    if choice not in CHOICES:
        raise ValueError("알 수 없는 꾸미기 항목입니다.")
    s = _settings(settings)
    s.setValue("accessory", choice)
    s.sync()
    return choice


def pack_root() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)) / "SyeonPet" / "wardrobe" / "packs"


def read_pack_choice(settings=None) -> str:
    name = _settings(settings).value("pack", "original", type=str)
    return name if name == "original" or re.fullmatch(r"pack-[0-9a-f]{32}", name or "") else "original"


def write_pack_choice(name, settings=None):
    if name != "original" and not re.fullmatch(r"pack-[0-9a-f]{32}", name):
        raise ValueError("올바르지 않은 외형 이름입니다.")
    s = _settings(settings)
    s.setValue("pack", name)
    s.sync()


def _hair_bounds(pm: QPixmap):
    """Use the biggest green region, not other green pixels in the frame."""
    key = pm.cacheKey()
    if key in _HAIR_CACHE:
        _HAIR_CACHE.move_to_end(key)
        return _HAIR_CACHE[key]
    result = _find_hair_bounds(pm)
    _HAIR_CACHE[key] = result
    if len(_HAIR_CACHE) > 512:
        _HAIR_CACHE.popitem(last=False)
    return result


def _find_hair_bounds(pm: QPixmap):
    image = pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = image.width(), image.height()
    if not width or not height:
        return None
    pixels, stride = bytes(image.bits()), image.bytesPerLine()
    green = set()
    row_pixels = stride // 4
    for i, (r, g, b, a) in enumerate(struct.iter_unpack("BBBB", pixels)):
        if (a > 150 and g > 100 and r < 140 and b < 100
                and g > r * 1.3 and g > b * 1.5):
            y, x = divmod(i, row_pixels)
            if x < width:
                green.add(y * width + x)
    biggest = []
    while green:
        stack = [green.pop()]
        component = []
        while stack:
            point = stack.pop()
            component.append(point)
            x, y = point % width, point // width
            for adjacent in (point - 1 if x else -1,
                             point + 1 if x + 1 < width else -1,
                             point - width if y else -1,
                             point + width if y + 1 < height else -1):
                if adjacent in green:
                    green.remove(adjacent)
                    stack.append(adjacent)
        if len(component) > len(biggest):
            biggest = component
    if len(biggest) < 20:
        return None
    xs = [i % width for i in biggest]
    ys = [i // width for i in biggest]
    return min(xs), min(ys), max(xs), max(ys)


def decorate_frame(frame: QPixmap, choice: str) -> QPixmap:
    """Return a composited frame; source assets are never modified."""
    if choice == "original" or choice not in CHOICES or frame.isNull():
        return frame
    if choice in PRESETS:
        return decorate_preset(frame, choice)
    bounds = _hair_bounds(frame)
    if bounds is None:
        return frame
    left, top, right, bottom = bounds
    x = left + (right - left) * 0.70
    y = top + (bottom - top) * 0.45
    scale = max(0.65, min(1.4, (right - left) / 80))
    result = frame.copy()
    p = QPainter(result)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(x, y)
    p.scale(scale, scale)
    p.setPen(QPen(QColor("#363046"), 2.5))
    if choice == "bow":
        p.setBrush(QColor("#7dd9ee"))
        p.drawPolygon(QPolygonF([QPointF(-3, 0), QPointF(-21, -11), QPointF(-19, 12)]))
        p.drawPolygon(QPolygonF([QPointF(3, 0), QPointF(21, -11), QPointF(19, 12)]))
        p.setBrush(QColor("#fff4c7"))
        p.drawEllipse(QPointF(0, 0), 5, 5)
    elif choice == "star":
        import math
        points = []
        for i in range(10):
            radius = 15 if i % 2 == 0 else 7
            angle = -math.pi / 2 + i * math.pi / 5
            points.append(QPointF(radius * math.cos(angle), radius * math.sin(angle)))
        p.setBrush(QColor("#ffd755"))
        p.drawPolygon(QPolygonF(points))
    else:
        p.setBrush(QColor("#405782"))
        p.drawPolygon(QPolygonF([QPointF(-22, -2), QPointF(0, -13),
                                QPointF(22, -2), QPointF(0, 9)]))
        p.drawLine(QPointF(-12, 4), QPointF(-12, 12))
        p.drawLine(QPointF(-12, 12), QPointF(12, 12))
        p.drawLine(QPointF(12, 12), QPointF(12, 4))
        p.setPen(QPen(QColor("#ffd755"), 2.5))
        p.drawLine(QPointF(19, -1), QPointF(19, 13))
    p.end()
    return result


class WardrobeWindow(QWidget):
    """Modeless chooser. Changes appear on the pet immediately."""

    def __init__(self, pet):
        super().__init__()
        self.pet = pet
        self.setWindowTitle("션이 옷장")
        self.resize(410, 450)
        self.setMinimumWidth(350)
        self.setStyleSheet("""
            QWidget { background: #fffdfa; color: #282532; font-family: 'Malgun Gothic'; font-size: 10pt; }
            QLabel#title { font-size: 17pt; font-weight: bold; }
            QLabel#hint { color: #514c5a; }
            QRadioButton { padding: 8px 4px; }
            QRadioButton::indicator { width: 15px; height: 15px; border: 2px solid #626075; border-radius: 9px; background: #fffdfa; }
            QRadioButton::indicator:checked { border: 4px solid #434a71; background: #fffdfa; }
            QPushButton { background: #434a71; color: white; border: 0; border-radius: 5px; padding: 8px 18px; }
            QPushButton:hover { background: #303958; }
            QPushButton:focus, QRadioButton:focus { outline: 2px solid #8a6acf; }
        """)
        outer = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)
        layout = QVBoxLayout(body)
        title = QLabel("션이 옷장")
        title.setObjectName("title")
        layout.addWidget(title)
        hint = QLabel("마음에 드는 옷을 선택하면 션에게 바로 적용돼요.\n"
                      "원래 모습을 고르면 언제든 돌아갈 수 있어요.\n"
                      "몸통 옷은 자세에 따라 가려질 수 있어요.\n"
                      "가져온 외형에 따라 옷이 보이지 않을 수 있어요.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        pack_line = QHBoxLayout()
        pack_line.addWidget(QLabel("외형 팩"))
        self.pack_combo = QComboBox()
        self.pack_combo.setAccessibleName("적용할 외형 팩")
        pack_line.addWidget(self.pack_combo, 1)
        add_pack = QPushButton("가져오기")
        add_pack.setToolTip("Sprite-Gen으로 완성한 로컬 팩 폴더를 가져옵니다.")
        add_pack.clicked.connect(self._choose_pack_folder)
        pack_line.addWidget(add_pack)
        layout.addLayout(pack_line)
        self.pack_status = QLabel(pet.wardrobe_pack_error or "완성된 로컬 Sprite-Gen 팩을 가져올 수 있어요.")
        self.pack_status.setWordWrap(True)
        self.pack_status.setObjectName("hint")
        layout.addWidget(self.pack_status)
        self._picker = None
        self._fill_packs()
        self.pack_combo.currentIndexChanged.connect(self._select_pack)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setAccessibleName("선택한 꾸미기 미리보기")
        self.preview.setMinimumHeight(130)
        layout.addWidget(self.preview)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for name, (label, description) in CHOICES.items():
            row = QRadioButton(label)
            row.setToolTip(description)
            row.setAccessibleDescription(description)
            row.setChecked(name == pet.wardrobe_choice)
            row.toggled.connect(lambda checked, key=name: self._select(key) if checked else None)
            self.group.addButton(row)
            self.buttons[name] = row
            layout.addWidget(row)
        layout.addStretch()
        footer = QHBoxLayout()
        footer.addStretch()
        close = QPushButton("닫기")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        layout.addLayout(footer)
        self._refresh_preview()

    def _select(self, choice):
        self.pet.set_wardrobe_choice(choice)
        self._refresh_preview()

    def _refresh_preview(self):
        frame = self.pet.sprites.states["idle"]["frames"][0]
        preview = decorate_frame(frame, self.pet.wardrobe_choice)
        self.preview.setPixmap(preview.scaled(150, 150, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))

    def _fill_packs(self):
        with QSignalBlocker(self.pack_combo):
            self.pack_combo.clear()
            self.pack_combo.addItem("기본 외형", "original")
            root = pack_root()
            if root.is_dir():
                for folder in sorted(root.iterdir(), key=lambda p: p.name):
                    if folder.is_dir() and re.fullmatch(r"pack-[0-9a-f]{32}", folder.name):
                        self.pack_combo.addItem("가져온 외형 " + folder.name[-6:], folder.name)
            index = self.pack_combo.findData(self.pet.wardrobe_pack)
            self.pack_combo.setCurrentIndex(max(0, index))

    def _select_pack(self):
        name = self.pack_combo.currentData()
        if not name:
            return
        try:
            self.pet.set_wardrobe_pack(name)
        except (ValueError, OSError) as exc:
            self.pack_status.setText(str(exc))
            with QSignalBlocker(self.pack_combo):
                self.pack_combo.setCurrentIndex(max(0, self.pack_combo.findData(self.pet.wardrobe_pack)))
            return
        self.pack_status.setText("선택한 외형을 적용했어요. 원본 파일은 그대로 보관돼요.")
        self._refresh_preview()

    def _choose_pack_folder(self):
        if self._picker is not None:
            self._picker.raise_()
            return
        picker = QFileDialog(self, "완성된 Sprite-Gen 팩 폴더 선택")
        picker.setFileMode(QFileDialog.FileMode.Directory)
        picker.setOption(QFileDialog.Option.ShowDirsOnly, True)
        picker.fileSelected.connect(self._import_pack)
        picker.finished.connect(lambda _: setattr(self, "_picker", None))
        self._picker = picker
        picker.open()

    def _import_pack(self, folder):
        from wardrobe_packs import import_sprite_pack
        try:
            result = import_sprite_pack(Path(folder), pack_root())
            self.pet.set_wardrobe_pack(result.name)
        except (ValueError, OSError) as exc:
            self.pack_status.setText("가져오지 못했어요: " + str(exc))
            return
        self._fill_packs()
        self.pack_status.setText("외형 팩을 가져와 적용했어요. 기존 파일은 그대로 보관돼요.")
        self._refresh_preview()
