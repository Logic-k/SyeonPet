"""SyeonPet pet module: companion care, petting tracker, quick action bar, and timer badge."""
from .companion_care import (
    DEFAULT_CARE_LINES,
    HeartParticle,
    OfficeCareScheduler,
    PettingTracker,
    paint_hearts,
)
from .quick_bar import (
    QuickActionBar,
    TimerBadge,
    format_badge_text,
    paint_timer_badge,
)

__all__ = [
    "DEFAULT_CARE_LINES",
    "HeartParticle",
    "OfficeCareScheduler",
    "PettingTracker",
    "QuickActionBar",
    "TimerBadge",
    "format_badge_text",
    "paint_hearts",
    "paint_timer_badge",
]
