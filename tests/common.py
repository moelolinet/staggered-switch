"""Shared test fixtures."""

from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

from custom_components.staggered_switch.schedule import Action, Schedule

ANCHOR = date(2025, 1, 6)
TZ = ZoneInfo("Asia/Shanghai")


def make_schedule(cycle_days: int = 8) -> Schedule:
    """Return a small but representative schedule."""
    return Schedule(
        anchor_date=ANCHOR,
        cycle_days=cycle_days,
        actions=(
            Action(1, time(6), "switch.turn_on", "switch.a"),
            Action(2, time(6), "switch.turn_on", "switch.a"),
            Action(8, time(23), "switch.turn_off", "switch.a"),
        ),
    )
