"""Onboarding tour — the pet walks to anchor spots and narrates steps."""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer

from behavior import BehaviorController
from bubble import SpeechBubble
from pet import PetWidget


class OnboardingTour(QObject):
    """Scripted walkthrough: pet ambles between screen anchors while the
    bubble (a child of the pet window) shows each step. The behavior machine
    is suspended meanwhile."""

    def __init__(self, pet: PetWidget, behavior: BehaviorController,
                 bubble: SpeechBubble, steps: list[dict]):
        super().__init__()
        self.pet = pet
        self.behavior = behavior
        self.bubble = bubble
        self.steps = steps
        self.index = -1
        self._arrived = False

        bubble.next_clicked.connect(self.next)
        bubble.prev_clicked.connect(self.prev)
        bubble.skip_clicked.connect(self.finish)

        self._tick = QTimer(self)
        self._tick.setInterval(30)
        self._tick.timeout.connect(self._update)

    @property
    def active(self) -> bool:
        return self.index >= 0

    # ---------- flow ----------

    def start(self):
        if self.active or not self.steps:
            return
        self.behavior.onboarding_active = True
        self.index = 0
        self._arrived = False
        self.pet.set_state("walk")
        self._tick.start()
        self.bubble.show_step(self._step_text(0), 0, len(self.steps), True)
        self.pet._relayout()

    def next(self):
        if self.index >= len(self.steps) - 1:
            self.finish()
            return
        self.index += 1
        self._arrived = False
        self.bubble.show_step(self._step_text(self.index), self.index,
                              len(self.steps), True)
        self.pet._relayout()

    def prev(self):
        if self.index <= 0:
            return
        self.index -= 1
        self._arrived = False
        self.bubble.show_step(self._step_text(self.index), self.index,
                              len(self.steps), True)
        self.pet._relayout()

    def finish(self):
        if not self.active:
            return
        self.index = -1
        self._tick.stop()
        self.bubble.hide()
        self.pet._relayout()
        self.behavior.onboarding_active = False
        self.behavior.play_one_shot("celebrate", then="idle")

    # ---------- internals ----------

    def _step_text(self, i: int) -> str:
        s = self.steps[i]
        body = s.get("text", "")
        if s.get("title"):
            body = f"<b>{s['title']}</b><br>{body}"
        return body + f"<br><span style='color:#9a8f84'>{i + 1} / {len(self.steps)}</span>"

    def _anchor_x(self, step: dict) -> int:
        bounds = self.pet.screen_bounds()
        anchor = step.get("anchor", "center")
        if anchor == "left":
            return bounds.left() + bounds.width() // 5
        if anchor == "right":
            return bounds.right() - bounds.width() // 5 - self.pet.width()
        return bounds.center().x() - self.pet.width() // 2

    def _update(self):
        if not self.active:
            return
        target = self._anchor_x(self.steps[self.index])
        dx = target - self.pet.x()
        step = self.behavior.WALK_SPEED * 0.03
        if abs(dx) > step + 1 and not self._arrived:
            self.pet.facing_left = dx < 0
            self.pet.move(self.pet.x() + int(step * (1 if dx > 0 else -1)),
                          self.pet.y())
            self.pet.set_state("walk")
        elif not self._arrived:
            self._arrived = True
            self.pet.set_state("wave" if self.index == 0 else "idle")
        self.pet._relayout()
