"""Companion care, petting detection, heart particle effects, and office rhythm scheduling.

Provides affectionate tactile responses and empathetic Korean office companion care
keyed to the natural rhythm of the Korean workday.
"""
from __future__ import annotations

import collections
from datetime import datetime, time as dtime
import random
import time
from typing import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter

PETTING_LINES: tuple[str, ...] = (
    "헤헤, 머리 쓰담쓰담 정말 좋아요! 💕",
    "응원 에너지 100% 충전 완료! 오늘도 화이팅이에요 🌱",
    "간지러워요 헤헤~ 잠깐 숨 고르고 가실래요?",
    "선생님 손길이 따뜻해요! 제가 옆에서 든든히 도울게요 ✨",
)

PASTEL_HEART_COLORS: tuple[QColor, ...] = (
    QColor(255, 130, 165),  # Soft pink
    QColor(255, 110, 120),  # Warm coral
    QColor(185, 145, 235),  # Lavender
    QColor(255, 170, 130),  # Peach
    QColor(255, 150, 190),  # Rose
)


class HeartParticle:
    """A floating, fading heart particle spawned by gentle petting."""

    def __init__(self, x: float, y: float, vx: float, vy: float, color: QColor, max_age: float = 0.9):
        self.x = float(x)
        self.y = float(y)
        self.vx = float(vx)
        self.vy = float(vy)
        self.color = color
        self.max_age = max(0.1, float(max_age))
        self.age = 0.0
        self.scale = random.uniform(0.85, 1.25)

    @property
    def alpha(self) -> float:
        progress = min(1.0, self.age / self.max_age)
        return max(0.0, 1.0 - (progress * progress))

    def update(self, dt: float) -> bool:
        """Advance particle physics. Returns True if particle is still alive."""
        self.age += max(0.0, dt)
        self.x += self.vx * dt
        self.y += self.vy * dt
        # Subtle air drag and buoyancy
        self.vx *= 0.96
        self.vy -= 12.0 * dt
        return self.age < self.max_age


def paint_hearts(painter: QPainter, particles: Sequence[HeartParticle]) -> None:
    """Render heart particles onto the painter canvas."""
    if not particles:
        return
    painter.save()
    font = painter.font()
    for p in particles:
        alpha = p.alpha
        if alpha <= 0.01:
            continue
        c = QColor(p.color)
        c.setAlphaF(alpha * 0.95)
        painter.setPen(c)
        sz = max(7, int(11 * p.scale))
        font.setPointSize(sz)
        font.setBold(True)
        painter.setFont(font)
        # Center the heart glyph at (p.x, p.y)
        rect = QRectF(p.x - 12, p.y - 12, 24, 24)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "♥")
    painter.restore()


class PettingTracker:
    """Detects gentle stroking over Syeon's head zone and tracks affection."""

    def __init__(self, cooldown: float = 2.5):
        self.cooldown = cooldown
        self.affection_points: int = 0
        self.particles: list[HeartParticle] = []
        self._history: collections.deque[tuple[float, float, float]] = collections.deque()
        self._last_trigger_time: float = -999.0
        self._last_line: str = ""

    @property
    def mood_label(self) -> str:
        pts = self.affection_points
        if pts < 3:
            return "기분 좋음 🌱"
        if pts < 6:
            return "신남 💛"
        if pts < 10:
            return "사랑받는 중 💕"
        return "집중 응원 중 🔥"

    def feed_point(self, x: float, y: float, sprite_h: float, now: float | None = None) -> tuple[bool, str | None]:
        """Process cursor position on mouse move without dragging.

        Only strokes in the upper head zone (y < sprite_h * 0.55) count.
        Returns (True, message) if a petting gesture is detected.
        """
        current = time.monotonic() if now is None else float(now)
        # Check head/leaf bounding zone
        if y >= sprite_h * 0.55:
            return False, None

        self._history.append((current, x, y))
        # Keep only the last 1.5 seconds of motion
        while self._history and (current - self._history[0][0]) > 1.5:
            self._history.popleft()

        if len(self._history) < 4:
            return False, None

        if (current - self._last_trigger_time) < self.cooldown:
            return False, None

        # Check horizontal reversals and cumulative distance
        reversals = 0
        last_sign = 0
        cum_dist = 0.0
        pts = list(self._history)
        for i in range(1, len(pts)):
            dx = pts[i][1] - pts[i - 1][1]
            dy = pts[i][2] - pts[i - 1][2]
            dist = (dx * dx + dy * dy) ** 0.5
            cum_dist += dist
            sign = 1 if dx > 1.0 else (-1 if dx < -1.0 else 0)
            if sign != 0:
                if last_sign != 0 and sign != last_sign:
                    reversals += 1
                last_sign = sign

        if reversals >= 2 or cum_dist >= 90.0:
            self._last_trigger_time = current
            self.affection_points += 1
            self._history.clear()
            self.spawn_hearts(x, y, count=random.randint(5, 7))

            # Pick non-repeating petting line
            choices = [line for line in PETTING_LINES if line != self._last_line]
            chosen = random.choice(choices)
            self._last_line = chosen
            return True, chosen

        return False, None

    def spawn_hearts(self, origin_x: float, origin_y: float, count: int = 5) -> None:
        """Spawn heart particles bursting gently around origin."""
        for _ in range(count):
            vx = random.uniform(-25.0, 25.0)
            vy = random.uniform(-65.0, -35.0)
            color = random.choice(PASTEL_HEART_COLORS)
            ox = origin_x + random.uniform(-10.0, 10.0)
            oy = origin_y + random.uniform(-6.0, 6.0)
            self.particles.append(HeartParticle(ox, oy, vx, vy, color, max_age=random.uniform(0.8, 1.1)))

    def update_particles(self, dt: float) -> None:
        """Advance all active particles and discard expired ones."""
        self.particles = [p for p in self.particles if p.update(dt)]


DEFAULT_CARE_LINES: dict[str, list[tuple[str, str | None]]] = {
    "morning": [
        ("좋은 아침이에요! 오늘 하루도 션과 함께 산뜻하게 시작해요 ☀️", "bow"),
        ("출근길 고생 많으셨어요! 따뜻한 차 한 잔으로 시작해 볼까요? ☕", "bow"),
        ("오늘도 빛나는 하루가 될 거예요! 옆에서 조용히 응원할게요 ✨", None),
        ("아침 공기가 상쾌하네요! 오늘 할 일 하나씩 차근차근 해봐요 🌱", None),
        ("새로운 하루의 시작! 션이 좋은 기운 가득 불어넣어 드릴게요 🍀", "bow"),
    ],
    "lunch": [
        ("선생님, 벌써 점심시간이에요! 맛있는 식사 든든하게 챙겨드세요 🍱", None),
        ("오늘 점심 메뉴는 정하셨나요? 든든하게 먹고 오후도 힘내요 🍲", None),
        ("잠시 모니터에서 눈을 떼고 맛있는 점심 드시고 오세요! 🍙", None),
        ("밥심으로 일하는 거 아시죠? 맛있는 한 끼 꼭 챙기세요 🥗", None),
        ("식사 맛있게 하시고, 따뜻한 햇살 아래 잠깐 걸어보시는 건 어때요? 🌿", None),
    ],
    "afternoon": [
        ("슬슬 나른해지는 오후예요! 기지개 한번 쭈욱 켜볼까요? 🙆", "stretch"),
        ("눈이 피로하실 땐 먼 창밖을 10초만 바라봐 주세요 🌿", None),
        ("잠깐 어깨 툭툭 털고, 시원한 물 한 잔 마시고 올까요? 💧", "stretch"),
        ("오후 집중 시간! 달콤한 간식이나 차 한 잔 어떠세요? 🍪", None),
        ("벌써 하루의 절반이 훌쩍 지났어요! 조금만 더 힘내요 💪", "stretch"),
    ],
    "overtime": [
        ("오늘 하루도 정말 수고 많으셨어요! 이제 편안한 저녁 보내세요 🌙", "bow"),
        ("퇴근 시간이 가까워졌어요! 오늘 마무리도 차근차근 잘 끝내봐요 🎒", None),
        ("야근 중이시라면 늦지 않게 들어가세요, 건강이 언제나 제일이에요 💛", None),
        ("오늘도 온 힘을 다하셨네요. 션이 칭찬 도장 쾅 찍어드릴게요! 💮", "bow"),
        ("지친 하루 끝, 오늘 저녁엔 푹 쉬면서 맛있는 것 드세요 🛋️", None),
    ],
    "general": [
        ("언제나 선생님 곁에 있어요. 필요하신 게 있으면 말씀해 주세요 🌱", None),
        ("션은 항상 선생님 편인 거 아시죠? 언제나 응원해요! 💛", None),
        ("작은 걸음이라도 매일 꾸준히 나아가는 모습이 멋져요 ✨", None),
        ("바쁜 일상 속에서도 잠시 숨 고르는 여유 잊지 마세요 🍃", None),
        ("오늘도 션과 함께해 주셔서 고마워요! 🍀", None),
    ],
}


class OfficeCareScheduler:
    """Selects contextual Korean office care messages without back-to-back repeats."""

    def __init__(self, history_limit: int = 5):
        self._history: collections.deque[str] = collections.deque(maxlen=history_limit)

    @staticmethod
    def get_period(now: datetime | None = None) -> str:
        """Determine Korean office workday period."""
        current = datetime.now() if now is None else now
        t = current.time()
        if dtime(8, 20) <= t < dtime(10, 0):
            return "morning"
        if dtime(11, 45) <= t < dtime(13, 10):
            return "lunch"
        if dtime(14, 30) <= t < dtime(16, 30):
            return "afternoon"
        if t >= dtime(17, 40) or t < dtime(4, 0):
            return "overtime"
        return "general"

    def pick_care_message(
        self,
        now: datetime | None = None,
        custom_care_lines: dict[str, list[tuple[str, str | None]]] | None = None,
    ) -> tuple[str, str | None]:
        """Return (care_message, suggested_motion) ensuring no back-to-back repeats."""
        period = self.get_period(now)
        catalog = custom_care_lines or DEFAULT_CARE_LINES
        lines = catalog.get(period) or catalog.get("general") or DEFAULT_CARE_LINES["general"]

        # Filter out recently used lines
        candidates = [pair for pair in lines if pair[0] not in self._history]
        if not candidates:
            # If all were used, pick any that isn't the immediate last one
            last = self._history[-1] if self._history else None
            candidates = [pair for pair in lines if pair[0] != last] or list(lines)

        chosen = random.choice(candidates)
        self._history.append(chosen[0])
        return chosen
