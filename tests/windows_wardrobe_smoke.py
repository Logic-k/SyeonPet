"""Isolated Qt wardrobe checks; never touch the user's settings or clipboard."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Lib" / "site-packages"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QScrollArea

from pet import PetWidget, SpriteBank
import wardrobe
from wardrobe import CHOICES, WardrobeWindow, decorate_frame


def rgba(pm):
    image = pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return bytes(image.bits())


def run():
    app = QApplication.instance() or QApplication([])
    out = Path(tempfile.mkdtemp(prefix="wardrobe-qa-", dir=ROOT.parent / "release-work"))
    wardrobe.pack_root = lambda: out / "packs"
    settings = QSettings(str(out / "synthetic.ini"), QSettings.Format.IniFormat)
    sprites = SpriteBank()
    pet = PetWidget(sprites, wardrobe_settings=settings)
    pet.tick.stop()
    missing = []
    edge_clipped = []
    start = time.perf_counter()
    counts = {}
    # Use every actual source frame. Byte comparisons only verify a mark exists,
    # not its artistic placement.
    for state, meta in sprites.states.items():
        counts[state] = len(meta["frames"])
        for index, frame in enumerate(meta["frames"]):
            source = rgba(frame)
            assert rgba(decorate_frame(frame, "original")) == source
            for choice in ("bow", "star", "cap"):
                changed = rgba(decorate_frame(frame, choice))
                if changed == source:
                    missing.append((state, index, choice))
                    continue
                width, height = frame.width(), frame.height()
                # Four edges only: a changed boundary pixel suggests the
                # accessory extends beyond the frame and can be clipped.
                edge = ((x, y) for y in range(height) for x in range(width)
                        if x == 0 or y == 0 or x == width - 1 or y == height - 1)
                if any(source[(y * width + x) * 4:(y * width + x + 1) * 4]
                       != changed[(y * width + x) * 4:(y * width + x + 1) * 4]
                       for x, y in edge):
                    edge_clipped.append((state, index, choice))
    elapsed = time.perf_counter() - start
    print("frames:", counts, "all-decoration-seconds:", round(elapsed, 2))
    print("unmodified frames:", missing)
    print("accessory touches image edge:", edge_clipped)

    # Selected frames create a contact sheet for human visual inspection.
    names = list(sprites.states)
    sheet = QImage(4 * 170, len(names) * 190, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#fffdfa"))
    paint = QPainter(sheet)
    for row, state in enumerate(names):
        frame = sprites.states[state]["frames"][0]
        for col, choice in enumerate(CHOICES):
            paint.drawText(col * 170 + 5, row * 190 + 17, state + " / " + choice)
            image = decorate_frame(frame, choice).scaled(160, 160,
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            paint.drawPixmap(col * 170 + 5, row * 190 + 23, image)
    paint.end()
    assert sheet.save(str(out / "contact-sheet.png"))

    window = WardrobeWindow(pet)
    window.resize(360, 450)
    window.show()
    app.processEvents()
    print("requested 360x450 -> actual", window.width(), window.height(),
          "minimumSizeHint", window.minimumSizeHint().width(), window.minimumSizeHint().height())
    assert window.grab().save(str(out / "window-360.png"))
    scroll = window.findChild(QScrollArea)
    if scroll:
        assert scroll.verticalScrollBar().maximum() > 0
        for label, button in window.buttons.items():
            scroll.ensureWidgetVisible(button)
            app.processEvents()
            assert not button.visibleRegion().isEmpty(), label
        print("PASS: each choice is reachable in the scroll area")
        assert window.grab().save(str(out / "window-360-scrolled.png"))
        window.resize(360, 360)
        app.processEvents()
        assert window.width() == 360 and window.height() == 360
        scroll.ensureWidgetVisible(window.buttons["cap"])
        app.processEvents()
        assert not window.buttons["cap"].visibleRegion().isEmpty()
        assert window.grab().save(str(out / "window-360x360-scrolled.png"))
    window.buttons["star"].click()
    app.processEvents()
    assert pet.wardrobe_choice == "star"
    assert settings.value("accessory") == "star"
    assert pet._pm_cache and pet._mask_cache
    for state, meta in sprites.states.items():
        pet.set_state(state)
        pet.frame_idx = len(meta["frames"]) - 1
        for facing in (False, True):
            pet.facing_left = facing
            pet._update_mask()
            scaled = pet._cur_scaled_pm()
            opaque = QRegion(scaled.mask())
            assert opaque.subtracted(pet._mask_region(pet.frame_idx)).isEmpty(), (state, facing)
            x, y, _, _ = pet._sprite_draw_rect()
            assert opaque.translated(x, y).subtracted(pet.mask()).isEmpty(), (state, facing)
    pet.set_state("idle")
    pet.frame_idx = 0
    pet.facing_left = True
    assert pet._scaled_pm(0).toImage() == pet._current_pm().scaled(
        pet.display_size, pet.display_size, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation).toImage()
    window.buttons["original"].click()
    assert pet.wardrobe_choice == "original" and settings.value("accessory") == "original"
    print("PASS: settings isolation, choice apply/reset, facing and regenerated caches")
    window.close()
    pet.close()
    print("OUTPUT:", out)
    if missing:
        raise AssertionError("some actual animation frames have no accessory")


if __name__ == "__main__":
    run()
