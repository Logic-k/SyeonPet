"""Manual Windows mouse QA fixture; uses only synthetic windows and no user data.

Run with included PySide6 and select the unique test windows using computer use.
Click through the pet's transparent upper-left area onto the background; click
the sleeping pet's opaque center to check waking. The fixture closes after 3 min.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Lib" / "site-packages"))

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from behavior import BehaviorController
from pet import PetWidget, SpriteBank


class TestBackground(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Syeon QA Background")
        self.resize(600, 400)
        self.count = 0
        self.status = QLabel("background_clicks=0; fully_asleep=True")
        self.instructions = QLabel("Synthetic test window: pet appears below this label.")
        layout = QVBoxLayout(self)
        layout.addWidget(self.instructions)
        layout.addWidget(self.status)
        layout.addStretch()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.count += 1
            self.update_status()

    def update_status(self):
        self.status.setText(f"background_clicks={self.count}; fully_asleep={controller.fully_asleep}")


if __name__ == "__main__":
    app = QApplication([])
    background = TestBackground()
    background.show()
    app.processEvents()
    pet = PetWidget(SpriteBank(), display_size=168)
    pet.setWindowTitle("Syeon QA Mascot")
    pet.motion_amp = 0
    controller = BehaviorController(pet)
    controller.still_sleep()
    controller.fully_asleep_changed.connect(lambda _: background.update_status())
    pet.move(background.mapToGlobal(QPoint(150, 150)))
    pet.show()
    app.processEvents()
    QTimer.singleShot(180000, app.quit)
    raise SystemExit(app.exec())
