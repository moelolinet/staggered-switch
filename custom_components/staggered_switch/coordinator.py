"""Runtime scheduler for Staggered Switch.

Model
-----
The scheduler keeps one marker, ``_handled_for``: the most recent instant it has
already dealt with (executed, or deliberately skipped because it was history at
install time).  Everything else is derived from it:

* a timer fires at instant ``t``   -> run it, set the marker to ``t``
* the minute tick sees a due instant -> run it only when ``t > marker``
* start-up sees the newest instant  -> run it only when ``t > marker``

That single rule makes execution idempotent and survives restarts without any
special cases.

Behaviour
---------
* Fresh install: the marker is set to the newest past instant and nothing is
  switched, so installing the integration never flips a device.
* Restart of Home Assistant: if an action passed while it was down, the newest
  one is applied so the device matches the roster again.  ``turn_on`` /
  ``turn_off`` are idempotent, so re-applying is harmless.
* Options change (reload while running): nothing is replayed, the timer is
  simply re-armed with the new configuration.

Re-arming
---------
Exactly one one-shot timer is armed.  A listener on the local minute boundary
re-evaluates the schedule, because timers fire at absolute instants and a DST
shift or a manual clock change would otherwise leave a stale wall clock time
armed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, CoreState, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, DUE_GRACE
from .schedule import Action, Occurrence, Schedule

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


class StaggeredSwitchScheduler:
    """Execute a :class:`Schedule` and keep a status snapshot."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, schedule: Schedule
    ) -> None:
        """Initialise the scheduler."""
        self.hass = hass
        self.schedule = schedule
        self.last_run: Occurrence | None = None
        self.last_error: str | None = None
        self.run_count = 0

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsub_clock: CALLBACK_TYPE | None = None
        self._unsub_start: CALLBACK_TYPE | None = None
        self._armed_for: datetime | None = None
        self._handled_for: datetime | None = None
        self._has_state = False
        self._listeners: list[Callable[[], None]] = []

    # -- lifecycle ------------------------------------------------------
    async def async_start(self) -> None:
        """Restore state, prepare convergence and arm the timer."""
        await self._async_restore()

        if not self._has_state:
            # Brand new entry: adopt the current position of the roster without
            # switching anything, so installing never flips a device.
            self._has_state = True
            newest = self.schedule.last_before(dt_util.now())
            if newest is not None:
                self._handled_for = newest.when
            await self._async_store()
        elif self.hass.state is CoreState.running:
            # Reloaded from the UI: leave the devices alone.
            _LOGGER.debug("Reload detected; not replaying past actions")
        else:
            # Entries are set up before the core finishes starting, so other
            # integrations (and their services) may not exist yet.  Converge
            # once everything is up.
            self._unsub_start = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._handle_started
            )

        self._arm_next()
        self._unsub_clock = async_track_time_change(
            self.hass, self._handle_clock_tick, second=0
        )
        self._notify()

    async def _handle_started(self, _event) -> None:
        """Run the start-up convergence once Home Assistant is running."""
        self._unsub_start = None
        await self._async_catch_up()
        self._arm_next()
        self._notify()

    async def async_stop(self) -> None:
        """Release every timer owned by the scheduler."""
        self._cancel_timer()
        if self._unsub_clock is not None:
            self._unsub_clock()
            self._unsub_clock = None
        if self._unsub_start is not None:
            self._unsub_start()
            self._unsub_start = None

    async def async_shutdown(self) -> None:
        """Stop the timers; the marker is already persisted after each change."""
        await self.async_stop()

    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Register a listener, returning an unsubscribe callback."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    # -- clock handling -------------------------------------------------
    async def _handle_clock_tick(self, _now: datetime) -> None:
        """Re-evaluate the schedule once a minute."""
        await self._async_run_due(dt_util.now())
        self._arm_next()
        self._notify()

    # -- scheduling -----------------------------------------------------
    @callback
    def _cancel_timer(self) -> None:
        """Cancel the pending timer if any."""
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None
        self._armed_for = None

    @callback
    def _arm_next(self) -> None:
        """Arm a one-shot timer for the next occurrence."""
        pending = self.schedule.upcoming(dt_util.now(), limit=1)
        target = pending[0].when if pending else None

        # Nothing changed: keeping the existing timer avoids cancel/re-create
        # churn right before it fires.
        if target == self._armed_for and self._unsub_timer is not None:
            return

        self._cancel_timer()
        if target is None:
            return

        self._armed_for = target
        self._unsub_timer = async_track_point_in_utc_time(
            self.hass, self._handle_due, target
        )

    async def _handle_due(self, _now: datetime) -> None:
        """Run whatever became due, then arm the next timer."""
        self._unsub_timer = None
        self._armed_for = None
        await self._async_run_due(dt_util.now())
        self._arm_next()
        self._notify()

    async def _async_run_due(self, now: datetime) -> None:
        """Execute what is due, tolerating a late timer.

        A normal timer fire is a fraction of a second late, so every action in
        the grace window runs.  A large delay (laptop suspend, clock jump) can
        skip many actions at once: replaying all of them would send a burst of
        stale commands, so only the newest one is applied to converge the state.
        """
        pending = self._pending(now)
        if not pending:
            return

        if (now - pending[-1].when) <= timedelta(seconds=DUE_GRACE):
            for occurrence in pending:
                await self._async_run(occurrence)
            return

        _LOGGER.debug(
            "Timer fired %.0f s late; applying only the newest of %d pending action(s)",
            (now - pending[-1].when).total_seconds(),
            len(pending),
        )
        await self._async_run(pending[-1])

    def _pending(self, now: datetime) -> list[Occurrence]:
        """Return occurrences between the handled marker and ``now``.

        The scan is bounded to one cycle: anything older than a full cycle has
        already been superseded by newer actions, and only the newest one is
        applied in that case anyway.
        """
        start = (
            self._handled_for
            if self._handled_for is not None
            else now - timedelta(seconds=DUE_GRACE)
        )
        oldest = now - timedelta(days=self.schedule.cycle_days)
        if start < oldest:
            start = oldest
        if now <= start:
            return []
        return self.schedule.between(start, now)

    async def _async_catch_up(self) -> None:
        """Converge to the state dictated by the most recent action."""
        newest = self.schedule.last_before(dt_util.now())
        if newest is None:
            return
        if self._handled_for is not None and self._handled_for >= newest.when:
            return

        _LOGGER.debug(
            "Catching up on %s (scheduled for %s)",
            newest.action.label,
            newest.when.isoformat(),
        )
        await self._async_run(newest)

    # -- execution ------------------------------------------------------
    async def _async_run(self, occurrence: Occurrence) -> None:
        """Call the service described by one occurrence."""
        action: Action = occurrence.action
        data = action.service_data()
        self._handled_for = occurrence.when
        self._has_state = True

        try:
            await self.hass.services.async_call(
                action.domain, action.service_name, data, blocking=True
            )
        except asyncio.CancelledError:
            # Shutting down: let the task unwind, but remember what happened.
            raise
        except Exception as err:  # noqa: BLE001
            # One failing action must never stop the rest of the batch, and an
            # integration raising something unexpected must still be reported
            # instead of aborting the whole run silently.
            self.last_error = f"{occurrence.when.isoformat()}: {err}"
            _LOGGER.exception(
                "Action %s for %s failed: %s",
                action.label,
                data.get(ATTR_ENTITY_ID),
                err,
            )
        else:
            self.last_error = None
            self.last_run = occurrence
            self.run_count += 1
            _LOGGER.debug(
                "Executed %s for %s (scheduled %s)",
                action.service,
                data.get(ATTR_ENTITY_ID),
                occurrence.when.isoformat(),
            )

        await self._async_store()
        self._notify()

    async def async_run_now(self, index: int) -> None:
        """Execute one configured action immediately (service call)."""
        action = self.schedule.actions[index]
        data = action.service_data()
        await self.hass.services.async_call(
            action.domain, action.service_name, data, blocking=True
        )
        self.last_run = Occurrence(when=dt_util.now(), action=action, cycle_index=0)
        self.last_error = None
        self.run_count += 1
        await self._async_store()
        self._notify()

    # -- persistence ----------------------------------------------------
    async def _async_restore(self) -> None:
        """Load the marker and counters.

        A damaged or partially written state file must never stop the
        schedule: every field is validated on its own and anything unusable is
        ignored, leaving the scheduler to start from a clean slate.
        """
        try:
            stored = await self._store.async_load()
        except (HomeAssistantError, OSError, ValueError):
            _LOGGER.warning("Could not read the stored schedule state")
            return

        if not isinstance(stored, dict):
            if stored is not None:
                _LOGGER.warning("Ignoring malformed schedule state: %r", type(stored))
            return

        self._has_state = True

        raw_count = stored.get("run_count", 0)
        try:
            self.run_count = max(0, int(raw_count or 0))
        except (TypeError, ValueError):
            _LOGGER.warning("Ignoring malformed run_count: %r", raw_count)
            self.run_count = 0

        if isinstance(raw := stored.get("handled_for"), str) and (
            parsed := dt_util.parse_datetime(raw)
        ):
            self._handled_for = parsed
        elif raw is not None:
            _LOGGER.warning("Ignoring malformed handled_for: %r", raw)

        raw_run = stored.get("last_run")
        if isinstance(raw_run, str) and (
            parsed_run := dt_util.parse_datetime(raw_run)
        ):
            match = self.schedule.between(
                parsed_run - timedelta(seconds=1), parsed_run
            )
            if match:
                self.last_run = match[-1]

    async def _async_store(self) -> None:
        """Persist the marker and counters."""
        payload: dict[str, Any] = {"run_count": self.run_count}
        if self._handled_for is not None:
            payload["handled_for"] = self._handled_for.isoformat()
        if self.last_run is not None:
            payload["last_run"] = self.last_run.when.isoformat()
        try:
            await self._store.async_save(payload)
        except (HomeAssistantError, OSError):
            _LOGGER.warning("Could not persist scheduler state", exc_info=True)

    # -- snapshots ------------------------------------------------------
    def _notify(self) -> None:
        """Notify listeners that the snapshot changed."""
        for listener in list(self._listeners):
            listener()

    def snapshot(self) -> dict[str, Any]:
        """Return the data consumed by entities and diagnostics.

        Everything is derived from a single pass over the schedule: computing
        the upcoming list once and reusing it for both ``next_run`` and
        ``upcoming`` avoids scanning the action list twice on every write.
        """
        now = dt_util.now()
        index, start, end = self.schedule.cycle_bounds(now)
        upcoming = self.schedule.upcoming(now, limit=3)

        return {
            "cycle_index": index,
            "cycle_day": self.schedule.day_in_cycle(now),
            "cycle_days": self.schedule.cycle_days,
            "cycle_start": start.isoformat(),
            "cycle_end": end.isoformat(),
            "next_run": upcoming[0].when.isoformat() if upcoming else None,
            "next_action": upcoming[0].action.as_dict() if upcoming else None,
            "last_run": self.last_run.when.isoformat() if self.last_run else None,
            "last_action": (
                self.last_run.action.as_dict() if self.last_run else None
            ),
            "last_error": self.last_error,
            "run_count": self.run_count,
            "upcoming": [item.as_dict() for item in upcoming],
        }
