"""Tests for the schedule arithmetic (no Home Assistant required)."""

from __future__ import annotations

from datetime import date, datetime, time
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from custom_components.staggered_switch.schedule import (
    Action,
    Occurrence,
    Schedule,
    ScheduleError,
)

from .common import ANCHOR, TZ, make_schedule


def test_cycle_index_and_day() -> None:
    """The cycle index and day follow the anchor date."""
    schedule = make_schedule()
    now = datetime(2025, 1, 6, 3, 0, tzinfo=TZ)

    assert schedule.cycle_index(now) == 0
    assert schedule.day_in_cycle(now) == 1


def test_cycle_index_advances_after_cycle() -> None:
    """A new cycle starts exactly on the anchor plus cycle length days."""
    schedule = make_schedule()
    boundary = datetime(2025, 1, 14, 0, 0, tzinfo=TZ)

    assert schedule.cycle_index(boundary) == 1
    assert schedule.day_in_cycle(boundary) == 1


def test_cycle_index_before_anchor_is_negative() -> None:
    """Dates before the anchor belong to negative cycles, not day 1."""
    schedule = make_schedule()
    before = datetime(2025, 1, 5, 12, 0, tzinfo=TZ)

    assert schedule.cycle_index(before) == -1
    assert schedule.day_in_cycle(before) == 8


def test_upcoming_skips_past_actions() -> None:
    """Only strictly future occurrences are returned, in time order."""
    schedule = make_schedule()
    now = datetime(2025, 1, 6, 6, 0, tzinfo=TZ)

    upcoming = schedule.upcoming(now, limit=2)

    assert [item.when.day for item in upcoming] == [7, 13]


def test_upcoming_returns_requested_count_for_single_action_cycle() -> None:
    """A one-action cycle still yields the requested number of occurrences."""
    schedule = Schedule(
        anchor_date=ANCHOR,
        cycle_days=1,
        actions=(Action(1, time(7), "switch.turn_on"),),
    )

    upcoming = schedule.upcoming(datetime(2025, 1, 6, 8, 0, tzinfo=TZ), limit=3)

    assert [item.when.day for item in upcoming] == [7, 8, 9]


def test_last_before_returns_most_recent() -> None:
    """The most recent occurrence is returned, including an exact match."""
    schedule = make_schedule()
    now = datetime(2025, 1, 14, 1, 0, tzinfo=TZ)

    last = schedule.last_before(now)

    assert last is not None
    assert last.when == datetime(2025, 1, 13, 23, 0, tzinfo=TZ)


def test_between_is_start_exclusive_end_inclusive() -> None:
    """``between`` excludes its start and includes its end."""
    schedule = make_schedule()
    found = schedule.between(
        datetime(2025, 1, 6, 6, 0, tzinfo=TZ),
        datetime(2025, 1, 8, 6, 0, tzinfo=TZ),
    )

    assert [item.when.day for item in found] == [7]


def test_actions_sharing_a_minute_are_all_returned() -> None:
    """Several actions on the same instant are preserved, not de-duplicated."""
    schedule = Schedule(
        anchor_date=ANCHOR,
        cycle_days=2,
        actions=(
            Action(1, time(6), "switch.turn_on", "switch.a"),
            Action(1, time(6), "switch.turn_off", "switch.b"),
        ),
    )

    found = schedule.between(
        datetime(2025, 1, 6, 0, 0, tzinfo=TZ),
        datetime(2025, 1, 7, 0, 0, tzinfo=TZ),
    )

    assert [item.action.entity_id for item in found] == ["switch.a", "switch.b"]


def test_wall_clock_survives_dst() -> None:
    """Actions keep their local clock time across a DST change."""
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    schedule = Schedule(
        anchor_date=date(2026, 3, 7),
        cycle_days=4,
        actions=(Action(2, time(6, 30), "switch.turn_on"),),
    )

    occurrence = schedule.occurrence(0, schedule.actions[0], ny)

    assert (occurrence.hour, occurrence.minute) == (6, 30)
    assert occurrence.date() == date(2026, 3, 8)


def test_empty_schedule_has_no_occurrences() -> None:
    """A schedule without actions reports nothing."""
    schedule = Schedule(anchor_date=ANCHOR, cycle_days=8, actions=())

    assert schedule.upcoming(datetime(2025, 1, 6, tzinfo=TZ)) == []
    assert schedule.last_before(datetime(2025, 1, 6, tzinfo=TZ)) is None


def test_action_outside_cycle_is_rejected() -> None:
    """An action beyond the cycle length is a configuration error."""
    with pytest.raises(ScheduleError):
        Schedule(
            anchor_date=ANCHOR,
            cycle_days=2,
            actions=(Action(3, time(6), "switch.turn_on"),),
        )


def test_describe_reports_next_run() -> None:
    """The snapshot exposes the next run and the cycle bounds."""
    snapshot = make_schedule().describe(datetime(2025, 1, 6, 1, 0, tzinfo=TZ))

    assert snapshot["cycle_day"] == 1
    assert snapshot["next_run"] == "2025-01-06T06:00:00+08:00"
    assert snapshot["cycle_start"] == "2025-01-06"
    assert snapshot["cycle_end"] == "2025-01-14"


def test_occurrence_serialisation() -> None:
    """Occurrences serialise into plain JSON-friendly data."""
    occurrence = Occurrence(
        when=datetime(2025, 1, 6, 6, 0, tzinfo=TZ),
        action=Action(1, time(6), "switch.turn_on", "switch.a"),
        cycle_index=0,
    )

    data = occurrence.as_dict()

    assert data["when"] == "2025-01-06T06:00:00+08:00"
    assert data["day"] == 1
    assert data["entity_id"] == "switch.a"
