"""Status sensor exposing the cycle position and the next run."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

try:  # Home Assistant 2024.6+ types this callback per config entry.
    from homeassistant.helpers.entity_platform import (  # noqa: F401
        AddConfigEntryEntitiesCallback,
    )
except ImportError:  # pragma: no cover - older Home Assistant
    AddConfigEntryEntitiesCallback = AddEntitiesCallback  # type: ignore[misc]
from homeassistant.util import dt as dt_util

from . import StaggeredSwitchConfigEntry
from .const import (
    ATTR_ACTIONS,
    ATTR_ACTIONS_TOTAL,
    ATTR_CYCLE_DAYS,
    ATTR_CYCLE_DAY,
    ATTR_CYCLE_END,
    ATTR_CYCLE_INDEX,
    ATTR_CYCLE_START,
    ATTR_LAST_ACTION,
    ATTR_LAST_ERROR,
    ATTR_LAST_RUN,
    ATTR_NEXT_ACTION,
    ATTR_NEXT_RUN,
    ATTR_RUN_COUNT,
    ATTR_UPCOMING,
    DOMAIN,
)
from .coordinator import StaggeredSwitchScheduler

# State attributes are copied on every write; exposing hundreds of actions
# bloats the state machine for no benefit.  The full list is available through
# the integration's options flow.
MAX_EXPOSED_ACTIONS = 50


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StaggeredSwitchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the status sensor belonging to an entry."""
    async_add_entities([StaggeredSwitchStatusSensor(entry)])


class StaggeredSwitchStatusSensor(SensorEntity):
    """Report the cycle position and the next scheduled run."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-clock"
    _attr_should_poll = False
    _attr_translation_key = "next_run"

    def __init__(self, entry: StaggeredSwitchConfigEntry) -> None:
        """Initialise the sensor."""
        self._entry = entry
        self._scheduler: StaggeredSwitchScheduler = entry.runtime_data.scheduler
        self._attr_unique_id = f"{entry.entry_id}_next_run"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Staggered Switch",
            model="Time cycle",
        )
        self._unsub: Callable[[], None] | None = None
        self._snapshot: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        """Subscribe to scheduler updates and seed the first snapshot."""
        self._snapshot = self._scheduler.snapshot()
        self._unsub = self._scheduler.async_add_listener(self._write_state)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from scheduler updates."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _write_state(self) -> None:
        """Publish a new state after the schedule changed."""
        # Compute once: both the state and the attributes read the snapshot.
        self._snapshot = self._scheduler.snapshot()
        self.async_write_ha_state()

    @property
    def native_value(self) -> StateType:
        """Return the next run as an aware timestamp."""
        raw = self._snapshot.get("next_run")
        return dt_util.parse_datetime(raw) if raw else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full schedule snapshot."""
        snapshot = self._snapshot
        return {
            ATTR_CYCLE_INDEX: snapshot.get("cycle_index"),
            ATTR_CYCLE_DAY: snapshot.get("cycle_day"),
            ATTR_CYCLE_DAYS: snapshot.get("cycle_days"),
            ATTR_CYCLE_START: snapshot.get("cycle_start"),
            ATTR_CYCLE_END: snapshot.get("cycle_end"),
            ATTR_NEXT_RUN: snapshot.get("next_run"),
            ATTR_NEXT_ACTION: snapshot.get("next_action"),
            ATTR_UPCOMING: snapshot.get("upcoming", []),
            ATTR_LAST_RUN: snapshot.get("last_run"),
            ATTR_LAST_ACTION: snapshot.get("last_action"),
            ATTR_LAST_ERROR: snapshot.get("last_error"),
            ATTR_RUN_COUNT: snapshot.get("run_count", 0),
            ATTR_ACTIONS: [
                action.as_dict() for action in self._scheduler.schedule.actions[:MAX_EXPOSED_ACTIONS]
            ],
            ATTR_ACTIONS_TOTAL: len(self._scheduler.schedule.actions),
        }
