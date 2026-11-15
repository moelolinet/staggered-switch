"""Framework independent schedule arithmetic.

A schedule is a repeating run of whole calendar days.  ``anchor_date`` is the
first day of cycle 0 and ``cycle_days`` is how many days a cycle lasts.  Within
a cycle an action is addressed by its 1 based day number and a wall clock time,
so "day 3 at 06:30" always means 06:30 on the third calendar day of that cycle.

Whole calendar days keep the model unambiguous (no window that starts in the
middle of a day) and keep clock times stable across daylight saving changes,
because every action is re-read as a local wall time on its own date.

This module deliberately has no Home Assistant imports so the date maths can be
unit tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
import math
from typing import Any, Final

ACTION_TURN_ON: Final = "turn_on"
ACTION_TURN_OFF: Final = "turn_off"
ACTION_CUSTOM: Final = "custom"
ACTION_TYPES: Final = (ACTION_TURN_ON, ACTION_TURN_OFF, ACTION_CUSTOM)

# Domains and the service pair that switches them.
#
# Most domains expose ``turn_on`` / ``turn_off``; covers and valves use
# ``open_*`` / ``close_*``, and buttons only support ``press``.  Offering a
# domain whose service does not exist would only fail at run time, so the
# mapping is explicit and shared by the UI and the scheduler.
TOGGLE_SERVICES: Final[dict[str, tuple[str, str]]] = {
    "automation": ("turn_on", "turn_off"),
    "fan": ("turn_on", "turn_off"),
    "humidifier": ("turn_on", "turn_off"),
    "input_boolean": ("turn_on", "turn_off"),
    "light": ("turn_on", "turn_off"),
    "media_player": ("turn_on", "turn_off"),
    "remote": ("turn_on", "turn_off"),
    "siren": ("turn_on", "turn_off"),
    "switch": ("turn_on", "turn_off"),
    "cover": ("open_cover", "close_cover"),
    "valve": ("open_valve", "close_valve"),
}

# Domains offered by the entity picker.  ``button`` is intentionally absent: it
# only supports "press", which an on/off action cannot express; users who need
# it can use a custom service call.
TOGGLE_DOMAINS: Final = tuple(TOGGLE_SERVICES)


def toggle_service(domain: str, turn_on: bool) -> str:
    """Return the fully qualified "on" or "off" service for a domain.

    Unknown domains fall back to ``turn_on`` / ``turn_off``, which is what a
    custom service call should use anyway.
    """
    on_name, off_name = TOGGLE_SERVICES.get(domain, ("turn_on", "turn_off"))
    return f"{domain}.{on_name if turn_on else off_name}"


def repair_service(service: str, entity_id: str | None) -> str:
    """Fix a stored service that cannot work for its entity.

    Older versions (and hand-edited options) could store ``cover.turn_on`` or
    ``valve.turn_off``.  Rewriting them here keeps such entries usable instead
    of failing at run time.
    """
    if not entity_id or "." not in service:
        return service

    domain, name = service.split(".", 1)
    if domain != entity_id.split(".", 1)[0]:
        return service
    if domain not in TOGGLE_SERVICES:
        return service

    on_name, off_name = TOGGLE_SERVICES[domain]
    if name == "turn_on" and on_name != "turn_on":
        return f"{domain}.{on_name}"
    if name == "turn_off" and off_name != "turn_off":
        return f"{domain}.{off_name}"
    return service


class ScheduleError(ValueError):
    """Raised when a schedule definition is not usable."""


def json_safe(value: Any) -> Any:
    """Return a JSON-serialisable copy of ``value``.

    Service data normally holds plain scalars, but a hand-edited entry (or a
    template-y payload) may contain ``datetime``, ``set`` or ``bytes``.  The
    scheduler passes such values straight to Home Assistant, yet the status
    sensor and diagnostics have to serialise them, so they are normalised here
    instead of failing later in an unrelated place.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


@dataclass(frozen=True, slots=True)
class Action:
    """One scheduled action inside a cycle."""

    day: int
    at: time
    service: str
    entity_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.day < 1:
            raise ScheduleError("day must be >= 1")
        if not self.service or "." not in self.service:
            raise ScheduleError(f"invalid service {self.service!r}")

    @property
    def domain(self) -> str:
        """Return the service domain."""
        return self.service.split(".", 1)[0]

    @property
    def service_name(self) -> str:
        """Return the service name without its domain."""
        return self.service.split(".", 1)[1]

    @property
    def label(self) -> str:
        """Return a short human readable label."""
        return f"{self.at.strftime('%H:%M')} {self.service}"

    def service_data(self) -> dict[str, Any]:
        """Return the payload passed to ``hass.services.async_call``."""
        payload: dict[str, Any] = dict(self.data)
        if self.entity_id:
            payload.setdefault("entity_id", self.entity_id)
        return payload

    def as_dict(self) -> dict[str, Any]:
        """Serialise the action for state attributes and diagnostics."""
        return {
            "day": self.day,
            "time": self.at.strftime("%H:%M:%S"),
            "service": self.service,
            "entity_id": self.entity_id,
            "data": json_safe(self.data),
        }


@dataclass(frozen=True, slots=True)
class Occurrence:
    """An action bound to one concrete point in time."""

    when: datetime
    action: Action
    cycle_index: int

    def as_dict(self) -> dict[str, Any]:
        """Serialise the occurrence."""
        return {"when": self.when.isoformat(), **self.action.as_dict()}


@dataclass(frozen=True, slots=True)
class Schedule:
    """A repeating run of calendar days with actions attached to it."""

    anchor_date: date
    cycle_days: int
    actions: tuple[Action, ...] = ()

    def __post_init__(self) -> None:
        if self.cycle_days < 1:
            raise ScheduleError("cycle_days must be >= 1")
        for action in self.actions:
            if action.day > self.cycle_days:
                raise ScheduleError(
                    f"day {action.day} is outside a {self.cycle_days} day cycle"
                )

    # -- cycle boundaries -----------------------------------------------
    def cycle_start(self, cycle_index: int) -> datetime:
        """Return the first day of ``cycle_index`` (may be negative)."""
        return self.anchor_date + timedelta(days=cycle_index * self.cycle_days)

    def cycle_index(self, now: datetime) -> int:
        """Return the cycle that contains ``now``.

        Negative indices are returned for dates before the anchor, which keeps
        the arithmetic consistent instead of silently clamping.
        """
        return math.floor((now.date() - self.anchor_date).days / self.cycle_days)

    def day_in_cycle(self, now: datetime) -> int:
        """Return the 1 based day number of ``now`` inside its cycle."""
        start = self.cycle_start(self.cycle_index(now))
        return (now.date() - start).days + 1

    def cycle_bounds(self, now: datetime) -> tuple[int, date, date]:
        """Return ``(index, first_day, first_day_of_next_cycle)``."""
        index = self.cycle_index(now)
        return index, self.cycle_start(index), self.cycle_start(index + 1)

    # -- occurrences ----------------------------------------------------
    def occurrence(
        self, cycle_index: int, action: Action, tz: tzinfo | None
    ) -> datetime | None:
        """Return the point in time of ``action`` inside ``cycle_index``.

        Returns ``None`` when the wall clock time does not exist on that day.
        This happens once a year in zones with daylight saving: ``02:30`` is
        skipped when the clock jumps from 02:00 to 03:00.  Resolving it to
        "the first valid instant" would pile every skipped action onto 03:00,
        so the action is simply not run that day - the same choice cron and
        the Windows task scheduler make.  It resumes on the next cycle.
        """
        day = self.cycle_start(cycle_index) + timedelta(days=action.day - 1)
        local = datetime.combine(day, action.at.replace(microsecond=0))
        return self._resolve_local(local, tz)

    @staticmethod
    def _resolve_local(local: datetime, tz: tzinfo | None) -> datetime | None:
        """Attach ``tz`` to a naive local time, or ``None`` if it does not exist.

        Two cases share the same shape: a wall clock time that occurs twice
        (clocks going back) and one that never occurs (clocks going forward).
        They are told apart by comparing the offsets of the two folds:

        * equal offsets          -> the time is unambiguous
        * fold 0 offset > fold 1 -> the time is repeated; take the first pass
        * fold 0 offset < fold 1 -> the time is skipped; report it as missing
        """
        if tz is None:
            return local

        first = local.replace(tzinfo=tz, fold=0)
        second = local.replace(tzinfo=tz, fold=1)
        first_offset = first.utcoffset()
        second_offset = second.utcoffset()

        if first_offset == second_offset:
            return first
        if first_offset is None or second_offset is None:
            return first
        if first_offset > second_offset:
            # Repeated hour: the earlier (fold 0) interpretation is used.
            return first
        return None

    def occurrences_in_cycle(
        self, cycle_index: int, tz: tzinfo | None
    ) -> list[Occurrence]:
        """Return every occurrence of one cycle, ordered by time."""
        occurrences = [
            Occurrence(
                when=when,
                action=action,
                cycle_index=cycle_index,
            )
            for action in self.actions
            if (when := self.occurrence(cycle_index, action, tz)) is not None
        ]
        occurrences.sort(key=lambda item: item.when.timestamp())
        return occurrences

    def upcoming(self, now: datetime, limit: int = 1) -> list[Occurrence]:
        """Return the next ``limit`` occurrences strictly after ``now``."""
        if not self.actions or limit <= 0:
            return []

        first = self.cycle_index(now)
        per_cycle = len(self.actions)
        # Enough cycles to hold ``limit`` actions, plus one because the cycle
        # currently in progress may already be partly in the past.
        cycles = -(-limit // per_cycle) + 1

        found: list[Occurrence] = []
        for offset in range(cycles):
            for occurrence in self.occurrences_in_cycle(first + offset, now.tzinfo):
                if occurrence.when > now:
                    found.append(occurrence)
                    if len(found) == limit:
                        return found
        return found

    def last_before(self, now: datetime) -> Occurrence | None:
        """Return the most recent occurrence at or before ``now``."""
        if not self.actions:
            return None

        first = self.cycle_index(now)
        for offset in range(3):
            found = [
                occurrence
                for occurrence in self.occurrences_in_cycle(
                    first - offset, now.tzinfo
                )
                if occurrence.when <= now
            ]
            if found:
                return max(found, key=lambda item: item.when.timestamp())
        return None

    def between(self, start: datetime, end: datetime) -> list[Occurrence]:
        """Return occurrences in ``(start, end]`` ordered by time."""
        if end <= start or not self.actions:
            return []

        span_days = (end.date() - start.date()).days + 1
        cycles = span_days // self.cycle_days + 2
        last_cycle = self.cycle_index(end)

        found: list[Occurrence] = []
        for offset in range(cycles):
            found.extend(
                occurrence
                for occurrence in self.occurrences_in_cycle(
                    last_cycle - offset, end.tzinfo
                )
                if start < occurrence.when <= end
            )
        found.sort(key=lambda item: item.when.timestamp())
        return found

    def actions_for_day(self, day: int) -> list[Action]:
        """Return the actions configured for one day of the cycle."""
        return [action for action in self.actions if action.day == day]

    # -- reporting ------------------------------------------------------
    def describe(self, now: datetime) -> dict[str, Any]:
        """Return a serialisable snapshot used by entities and diagnostics."""
        index, start, end = self.cycle_bounds(now)
        upcoming = self.upcoming(now, limit=1)
        return {
            "cycle_index": index,
            "cycle_day": self.day_in_cycle(now),
            "cycle_days": self.cycle_days,
            "cycle_start": start.isoformat(),
            "cycle_end": end.isoformat(),
            "next_run": upcoming[0].when.isoformat() if upcoming else None,
            "next_action": upcoming[0].action.as_dict() if upcoming else None,
        }
