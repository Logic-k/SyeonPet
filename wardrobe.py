"""Local, reversible accessories for Syeon's original sprite frames."""
from __future__ import annotations

import re
import struct
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QPointF, QSettings, QSignalBlocker, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QPushButton, QRadioButton, QScrollArea,
                               QSizePolicy, QSlider, QVBoxLayout, QWidget)

from wardrobe_presets import PRESETS, decorate_preset


CHOICES = {
    "original": ("기본 모습", "원래 션의 모습이에요."),
    "bow": ("하늘 리본", "산뜻한 하늘색 리본을 달아요."),
    "star": ("반짝 별", "작은 금빛 별 머리핀을 달아요."),
    "cap": ("학사모", "션과 함께 배우는 날의 작은 모자예요."),
    **PRESETS,
}
_HAIR_CACHE = OrderedDict()

_MOTION_NAMES = {
    "idle": "가만히 서기", "walk": "걷기", "wave": "인사하기",
    "sleep": "앉아서 자기", "drag": "끌려가기", "celebrate": "기뻐하기",
    "jump": "점프하기", "dance": "춤추기", "liesleep": "누워 자기",
    "still": "엎드려 자기", "thinking": "고민하기",
    "stretch": "기지개", "bow": "꾸벅 인사",
    "sleep_bridge": "연결 · 눕기", "wake_bridge": "연결 · 일어나기",
    "turn_bridge": "연결 · 옆으로 돌기", "turn_front": "연결 · 정면으로 돌기",
}


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
        self.setMinimumSize(350, 300)
        self.setStyleSheet("""
            QWidget { background: #fffdfa; color: #282532; font-family: 'Malgun Gothic'; font-size: 10pt; }
            QLabel#title { font-size: 17pt; font-weight: bold; }
            QLabel#hint { color: #514c5a; }
            QComboBox { background: #ffffff; color: #282532; border: 1px solid #777183; border-radius: 4px; padding: 4px 8px; min-height: 22px; }
            QComboBox:focus { border: 2px solid #434a71; padding: 3px 7px; }
            QRadioButton { padding: 4px; }
            QRadioButton::indicator { width: 15px; height: 15px; border: 2px solid #626075; border-radius: 9px; background: #fffdfa; }
            QRadioButton::indicator:checked { border: 2px solid #434a71; background: #434a71; }
            QCheckBox { spacing: 6px; }
            QCheckBox::indicator { width: 16px; height: 16px; border: 2px solid #626075; border-radius: 3px; background: #fffdfa; }
            QCheckBox::indicator:checked { border: 2px solid #434a71; background: #434a71; }
            QPushButton { background: #434a71; color: white; border: 0; border-radius: 5px; padding: 6px 10px; }
            QPushButton:hover { background: #303958; }
            QPushButton:disabled { background: #777183; color: #ffffff; }
            QPushButton:focus, QRadioButton:focus, QCheckBox:focus { outline: 2px solid #8a6acf; }
            QSlider::groove:horizontal { height: 5px; background: #777183; border-radius: 2px; }
            QSlider::handle:horizontal { width: 16px; height: 16px; margin: -6px 0; background: #434a71; border-radius: 8px; }
            QSlider:focus::handle:horizontal { background: #8a6acf; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        self.scroll = QScrollArea(self)
        self.scroll.setAccessibleName("션 옷장 선택 영역")
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        title = QLabel("션이 옷장")
        title.setObjectName("title")
        layout.addWidget(title)
        hint = QLabel("옷을 선택하면 바로 적용돼요.\n"
                      "가져온 외형이나 일부 자세에서는 옷이 가려질 수 있어요.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        pack_form = QFormLayout()
        pack_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        pack_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        pack_field = QWidget()
        pack_line = QHBoxLayout(pack_field)
        pack_line.setContentsMargins(0, 0, 0, 0)
        self.pack_combo = QComboBox()
        self.pack_combo.setAccessibleName("적용할 외형 팩")
        pack_line.addWidget(self.pack_combo, 1)
        self.add_pack = QPushButton("가져오기")
        self.add_pack.setAccessibleName("로컬 외형 팩 가져오기")
        self.add_pack.setToolTip("Sprite-Gen으로 완성한 로컬 팩 폴더를 가져옵니다.")
        self.add_pack.clicked.connect(self._choose_pack_folder)
        pack_line.addWidget(self.add_pack)
        pack_label = QLabel("외형 팩")
        pack_label.setBuddy(self.pack_combo)
        pack_form.addRow(pack_label, pack_field)
        layout.addLayout(pack_form)
        self.pack_status = QLabel(pet.wardrobe_pack_error or "완성된 로컬 Sprite-Gen 팩을 가져올 수 있어요.")
        self.pack_status.setWordWrap(True)
        self.pack_status.setObjectName("hint")
        layout.addWidget(self.pack_status)
        self._picker = None
        self._fill_packs()
        self.pack_combo.currentIndexChanged.connect(self._select_pack)
        choices = QFormLayout()
        choices.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        choices.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        choice_label = QLabel("옷 빠른 선택")
        self.choice_combo = QComboBox()
        self.choice_combo.setAccessibleName("입힐 옷 빠른 선택")
        choice_label.setBuddy(self.choice_combo)
        for key, (label, _description) in CHOICES.items():
            self.choice_combo.addItem(label, key)
        self.choice_combo.setCurrentIndex(max(0, self.choice_combo.findData(pet.wardrobe_choice)))
        choices.addRow(choice_label, self.choice_combo)
        motion_label = QLabel("움직임 미리보기")
        self.motion_combo = QComboBox()
        self.motion_combo.setAccessibleName("미리 볼 움직임")
        motion_label.setBuddy(self.motion_combo)
        choices.addRow(motion_label, self.motion_combo)
        layout.addLayout(choices)
        for combo in (self.pack_combo, self.choice_combo, self.motion_combo):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(6)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setAccessibleName("선택한 꾸미기 미리보기")
        self.preview.setMinimumHeight(130)
        layout.addWidget(self.preview)
        preview_line = QHBoxLayout()
        self.preview_play = QPushButton("재생")
        self.preview_play.setAccessibleName("움직임 미리보기 재생")
        self.preview_play.setAccessibleDescription("선택한 움직임을 반복해서 미리 봅니다.")
        self.preview_play.setToolTip("움직임을 반복해서 미리 봐요.")
        # Reserve the longest label before the first layout, including native metrics.
        self.preview_play.ensurePolished()
        self.preview_play.setText("일시정지")
        self.preview_play.setMinimumWidth(self.preview_play.sizeHint().width())
        self.preview_play.setText("재생")
        self.preview_play.clicked.connect(self._toggle_preview)
        preview_line.addWidget(self.preview_play)
        self.preview_flip = QCheckBox("좌우 반전")
        self.preview_flip.setAccessibleName("미리보기 좌우 반전")
        self.preview_flip.setToolTip("미리보기만 반전하며 실제 션의 움직임은 바꾸지 않아요.")
        self.preview_flip.toggled.connect(self._refresh_preview)
        preview_line.addWidget(self.preview_flip)
        self.preview_count = QLabel()
        self.preview_count.setAccessibleName("미리보기 프레임 위치")
        preview_line.addStretch()
        preview_line.addWidget(self.preview_count)
        layout.addLayout(preview_line)
        self.preview_slider = QSlider(Qt.Orientation.Horizontal)
        self.preview_slider.setAccessibleName("미리보기 프레임 선택")
        self.preview_slider.setTracking(True)
        self.preview_slider.valueChanged.connect(self._refresh_preview)
        layout.addWidget(self.preview_slider)
        self.repeat_hint = QLabel("미리보기는 반복 재생돼요.")
        self.repeat_hint.setObjectName("hint")
        self.repeat_hint.setWordWrap(True)
        layout.addWidget(self.repeat_hint)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._advance_preview)
        self._preview_playing = False
        self._reduced_motion = False
        self._fill_motions()
        self.motion_combo.currentIndexChanged.connect(self._motion_changed)
        self.group = QButtonGroup(self)
        self.buttons = {}
        layout.addWidget(QLabel("옷 목록"))
        for name, (label, description) in CHOICES.items():
            row = QRadioButton(label)
            row.setToolTip(description)
            row.setAccessibleName(label)
            row.setAccessibleDescription(description)
            row.setChecked(name == pet.wardrobe_choice)
            row.toggled.connect(lambda checked, key=name: self._select(key) if checked else None)
            self.group.addButton(row)
            self.buttons[name] = row
            layout.addWidget(row)
        self.choice_combo.currentIndexChanged.connect(self._choose_from_combo)
        layout.addStretch()
        footer = QHBoxLayout()
        footer.addStretch()
        self.close_button = QPushButton("닫기")
        self.close_button.setAccessibleName("션 옷장 닫기")
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)
        order = (self.pack_combo, self.add_pack, self.choice_combo, self.motion_combo,
                 self.preview_play, self.preview_flip, self.preview_slider,
                 *self.buttons.values(), self.close_button)
        for previous, following in zip(order, order[1:]):
            QWidget.setTabOrder(previous, following)
        self._refresh_preview()

    def _fit_to_work_area(self):
        geo = self.pet.sprite_rect_global()
        screen = QGuiApplication.screenAt(geo.center()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        self._fit_to_bounds(screen.availableGeometry())

    def _fit_to_bounds(self, bounds):
        """Account for native frame margins, including negative monitor origins."""
        if bounds.isEmpty():
            return
        frame = self.frameGeometry()
        extra_width = max(0, frame.width() - self.width())
        extra_height = max(0, frame.height() - self.height())
        available_width = max(1, bounds.width() - extra_width - 8)
        available_height = max(1, bounds.height() - extra_height - 8)
        self.setMinimumSize(min(350, available_width), min(300, available_height))
        self.resize(min(self.width(), available_width), min(self.height(), available_height))
        frame = self.frameGeometry()
        self.move(max(bounds.left() + 4, min(frame.x(), bounds.right() + 1 - frame.width() - 4)),
                  max(bounds.top() + 4, min(frame.y(), bounds.bottom() + 1 - frame.height() - 4)))

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_to_work_area()
        QTimer.singleShot(0, self, self._fit_to_work_area)
        self.pack_combo.setFocus(Qt.FocusReason.OtherFocusReason)

    def _select(self, choice):
        self.pet.set_wardrobe_choice(choice)
        with QSignalBlocker(self.choice_combo):
            self.choice_combo.setCurrentIndex(max(0, self.choice_combo.findData(choice)))
        self._refresh_preview()

    def _choose_from_combo(self):
        choice = self.choice_combo.currentData()
        if choice in self.buttons:
            self.buttons[choice].setChecked(True)

    def _preview_meta(self):
        source = self.pet.sprites.states.get(self.motion_combo.currentData()) or {}
        return source.get("playback", source)

    def _refresh_preview(self):
        meta = self._preview_meta()
        if not meta or not meta.get("frames"):
            self.preview.clear()
            self.preview_count.setText("0 / 0")
            self.preview_count.setAccessibleName("미리보기 프레임이 없어요")
            return
        frames = meta["frames"]
        index = min(self.preview_slider.value(), len(frames) - 1)
        preview = self.pet._frame_pm(index, self.motion_combo.currentData(),
                                     self.preview_flip.isChecked())
        if offset := meta.get("render_offset_y", 0):
            canvas = QPixmap(preview.size())
            canvas.fill(Qt.GlobalColor.transparent)
            painter = QPainter(canvas)
            painter.drawPixmap(0, offset, preview)
            painter.end()
            preview = canvas
        self.preview.setPixmap(preview.scaled(150, 150, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
        self.preview_count.setText(f"{index + 1} / {len(frames)}")
        self.preview_count.setAccessibleName(f"미리보기 프레임 {index + 1} / {len(frames)}")

    def _fill_motions(self):
        previous = self.motion_combo.currentData()
        with QSignalBlocker(self.motion_combo):
            self.motion_combo.clear()
            for state, name in _MOTION_NAMES.items():
                meta = self.pet.sprites.states.get(state)
                if meta and meta.get("frames"):
                    self.motion_combo.addItem(name, state)
            selected = self.motion_combo.findData(previous)
            self.motion_combo.setCurrentIndex(selected if selected >= 0 else 0)
        self._motion_changed()

    def _motion_changed(self):
        meta = self._preview_meta()
        count = len(meta.get("frames", ()))
        self.preview_count.setMinimumWidth(
            self.preview_count.fontMetrics().horizontalAdvance(f"{count} / {count}"))
        with QSignalBlocker(self.preview_slider):
            self.preview_slider.setRange(0, max(0, count - 1))
            self.preview_slider.setValue(0)
        self.preview_slider.setEnabled(count > 1)
        self.preview_play.setEnabled(count > 1 and not self._reduced_motion)
        self._refresh_preview()
        if self._preview_playing:
            self._schedule_preview()

    def _toggle_preview(self):
        if self._reduced_motion:
            return
        self._preview_playing = not self._preview_playing
        self.preview_play.setText("일시정지" if self._preview_playing else "재생")
        self.preview_play.setAccessibleName("움직임 미리보기 일시정지" if self._preview_playing
                                            else "움직임 미리보기 재생")
        if self._preview_playing:
            self._schedule_preview()
        else:
            self._preview_timer.stop()

    def _schedule_preview(self):
        meta = self._preview_meta()
        durations = meta.get("durations") or []
        index = self.preview_slider.value()
        duration = durations[index % len(durations)] if durations else 150
        self._preview_timer.start(max(20, int(duration)))

    def _advance_preview(self):
        if not self._preview_playing:
            return
        self.preview_slider.setValue((self.preview_slider.value() + 1) % (self.preview_slider.maximum() + 1))
        self._schedule_preview()

    def _stop_preview(self):
        self._preview_timer.stop()
        self._preview_playing = False
        self.preview_play.setText("재생")
        self.preview_play.setAccessibleName("움직임 미리보기 재생")

    def set_reduced_motion(self, enabled: bool):
        self._reduced_motion = enabled
        if enabled:
            self._stop_preview()
        self.preview_play.setEnabled(self.preview_slider.maximum() > 0 and not enabled)
        self.preview_play.setToolTip(
            "동작 줄이기 적용 중 · 프레임을 직접 선택해 보세요."
            if enabled else "움직임을 반복해서 미리 봐요.")
        self.preview_play.setAccessibleDescription(
            self.preview_play.toolTip() if enabled else "선택한 움직임을 반복해서 미리 봅니다.")
        self.repeat_hint.setText(self.preview_play.toolTip() if enabled
                                 else "미리보기는 반복 재생돼요.")

    def hideEvent(self, event):
        self._stop_preview()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._stop_preview()
        super().closeEvent(event)

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
        self._fill_motions()

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
        self._fill_motions()
