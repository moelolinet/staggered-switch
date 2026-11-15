"""Staggered Switch - time based, cycle aware device switching."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .config import ConfigError, build_schedule
from .const import CONF_RULE_INDEX, DOMAIN, PLATFORMS
from .coordinator import StaggeredSwitchScheduler

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_RUN_ACTION = "run_action"

ATTR_ENTRY_ID = "entry_id"


@dataclass(slots=True)
class StaggeredSwitchData:
    """Runtime objects stored on the config entry."""

    scheduler: StaggeredSwitchScheduler


StaggeredSwitchConfigEntry = ConfigEntry[StaggeredSwitchData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration level service."""
    hass.data.setdefault(DOMAIN, {})

    async def _async_run_action(call: ServiceCall) -> None:
        """Run one configured action immediately."""
        entry_id = call.data.get(ATTR_ENTRY_ID)
        data: StaggeredSwitchData | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if data is None:
            raise ConfigEntryError(f"Unknown entry id {entry_id!r}")

        raw_index = call.data.get(CONF_RULE_INDEX, 0)
        try:
            index = int(raw_index)
        except (TypeError, ValueError) as err:
            raise ConfigEntryError(
                f"rule_index must be a whole number, got {raw_index!r}"
            ) from err

        # A negative index would silently address the last action, which is
        # never what the caller meant.
        if index < 0:
            raise ConfigEntryError("rule_index must be zero or greater")
        if index >= len(data.scheduler.schedule.actions):
            raise ConfigEntryError(
                f"There is no action at index {index} "
                f"(this cycle has {len(data.scheduler.schedule.actions)})"
            )

        try:
            await data.scheduler.async_run_now(index)
        except (HomeAssistantError, ValueError) as err:
            raise HomeAssistantError(f"Action failed: {err}") from err

    if not hass.services.has_service(DOMAIN, SERVICE_RUN_ACTION):
        hass.services.async_register(DOMAIN, SERVICE_RUN_ACTION, _async_run_action)

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: StaggeredSwitchConfigEntry
) -> bool:
    """Set up one configured cycle."""
    try:
        schedule = build_schedule(entry.options or entry.data)
    except ConfigError as err:
        raise ConfigEntryError(f"Invalid schedule configuration: {err}") from err

    scheduler = StaggeredSwitchScheduler(hass, entry.entry_id, schedule)
    entry.runtime_data = StaggeredSwitchData(scheduler=scheduler)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.runtime_data

    try:
        await scheduler.async_start()
    except (HomeAssistantError, OSError, ValueError) as err:
        # Expected, recoverable start-up problems (storage unavailable, bad
        # persisted payload).  Anything else is a bug and must not be hidden
        # behind an endless retry loop.
        await scheduler.async_stop()
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        raise ConfigEntryNotReady(f"Could not start the schedule: {err}") from err

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: StaggeredSwitchConfigEntry
) -> bool:
    """Unload one configured cycle."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.scheduler.async_shutdown()
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded


async def _async_reload_entry(
    hass: HomeAssistant, entry: StaggeredSwitchConfigEntry
) -> None:
    """Reload the entry after its options changed."""
    await hass.config_entries.async_reload(entry.entry_id)
