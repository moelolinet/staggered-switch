"""Translate config entry data into a :class:`~.schedule.Schedule`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
import logging
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_ANCHOR_DATE,
    CONF_CYCLE_DAYS,
    CONF_RULE_DATA,
    CONF_RULE_DAY,
    CONF_RULE_ENABLED,
    CONF_RULE_ENTITY,
    CONF_RULE_SERVICE,
    CONF_RULE_TIME,
    CONF_RULES,
    MAX_CYCLE_DAYS,
    MIN_CYCLE_DAYS,
)
from .schedule import Action, Schedule, ScheduleError, repair_service

_LOGGER = logging.getLogger(__name__)


class ConfigError(ValueError):
    """Raised when stored options cannot be turned into a schedule."""


def parse_time(value: Any) -> time:
    """Parse a time from a string, ``time`` or ``datetime``."""
    if isinstance(value, datetime):
        return value.time().replace(microsecond=0)
    if isinstance(value, time):
        return value.replace(microsecond=0)
    if isinstance(value, str):
        parsed = dt_util.parse_time(value)
        if parsed is not None:
            return parsed.replace(microsecond=0)
    raise ConfigError(f"invalid time {value!r}")


def parse_date(value: Any) -> date:
    """Parse a date from a string, ``date`` or ``datetime``.

    A full ISO timestamp is accepted too, because some tooling stores the
    anchor as ``2025-01-06T00:00:00``.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        if (parsed := dt_util.parse_date(value)) is not None:
            return parsed
        if (stamp := dt_util.parse_datetime(value)) is not None:
            return stamp.date()
    raise ConfigError(f"invalid date {value!r}")


def parse_day(value: Any) -> int:
    """Parse a 1 based day number, rejecting non-integer input.

    Silently truncating ``2.9`` to ``2`` would move an action to a different
    day without telling the user, so fractional values are refused.
    """
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise ConfigError(f"invalid day {value!r}") from err
    if not number.is_integer():
        raise ConfigError(f"day must be a whole number, got {value!r}")
    return int(number)


def normalise_rules(rules: Any) -> list[dict[str, Any]]:
    """Return the stored rules as a flat list of dictionaries."""
    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
        return []
    return [dict(rule) for rule in rules if isinstance(rule, Mapping)]


def build_schedule(options: Mapping[str, Any]) -> Schedule:
    """Build a schedule from stored options, skipping unusable rules.

    A single malformed rule must not take the whole schedule down, so bad
    entries are logged and dropped while the rest keeps running.
    """
    try:
        anchor = parse_date(options.get(CONF_ANCHOR_DATE))
    except ConfigError as err:
        raise ConfigError(f"anchor date: {err}") from err

    raw_cycle = options.get(CONF_CYCLE_DAYS, 1)
    try:
        cycle_days = int(raw_cycle)
    except (TypeError, ValueError) as err:
        raise ConfigError(f"cycle days: {raw_cycle!r} is not a number") from err

    if not MIN_CYCLE_DAYS <= cycle_days <= MAX_CYCLE_DAYS:
        raise ConfigError(
            f"cycle days must be between {MIN_CYCLE_DAYS} and {MAX_CYCLE_DAYS}"
        )

    actions: list[Action] = []
    for position, rule in enumerate(normalise_rules(options.get(CONF_RULES))):
        if not rule.get(CONF_RULE_ENABLED, True):
            continue
        try:
            entity_id = rule.get(CONF_RULE_ENTITY) or None
            action = Action(
                day=parse_day(rule[CONF_RULE_DAY]),
                at=parse_time(rule[CONF_RULE_TIME]),
                service=repair_service(
                    str(rule[CONF_RULE_SERVICE]), entity_id
                ),
                entity_id=entity_id,
                data=dict(rule.get(CONF_RULE_DATA) or {}),
            )
        except (KeyError, TypeError, ValueError, ScheduleError) as err:
            _LOGGER.warning("Skipping rule %s: %s", position + 1, err)
            continue
        if action.day > cycle_days:
            _LOGGER.warning(
                "Skipping rule %s: day %s is outside a %s day cycle",
                position + 1,
                action.day,
                cycle_days,
            )
            continue
        actions.append(action)

    try:
        return Schedule(
            anchor_date=anchor, cycle_days=cycle_days, actions=tuple(actions)
        )
    except ScheduleError as err:
        raise ConfigError(str(err)) from err
