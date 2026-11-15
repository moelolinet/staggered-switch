"""End-to-end checks against a real Home Assistant core.

These tests boot the actual Home Assistant core and drive the integration the
same way the UI does.  They deliberately avoid
``pytest-homeassistant-custom-component`` because that plugin requires
Unix-only modules (``fcntl``, ``resource``).

Usage::

    python -m tests.verify

Requirements: an environment where ``homeassistant`` is importable, plus
``voluptuous`` and ``tzdata`` (all installed with Home Assistant).

Every check asserts instead of printing, so a failure is visible in the exit
code.  It deliberately avoids pytest-homeassistant-custom-component, which
needs Unix-only modules (fcntl / resource).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
import pathlib
import shutil
import sys
import tempfile
from zoneinfo import ZoneInfo

import voluptuous as vol

import homeassistant  # noqa: F401  (installs the probatio shim first)
from homeassistant import bootstrap, core, loader
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.staggered_switch.config import ConfigError, build_schedule
from custom_components.staggered_switch.schedule import (
    Action,
    Schedule,
    ScheduleError,
)

PROJECT = pathlib.Path(__file__).resolve().parent.parent
DOMAIN = "staggered_switch"
TZ = ZoneInfo("Asia/Shanghai")
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


# --------------------------------------------------------------- pure logic
def test_schedule_logic() -> None:
    print("schedule arithmetic")
    schedule = Schedule(
        anchor_date=datetime(2025, 1, 6).date(),
        cycle_days=8,
        actions=(
            Action(1, time(6), "switch.turn_on", "switch.a"),
            Action(2, time(6), "switch.turn_on", "switch.a"),
            Action(8, time(23), "switch.turn_off", "switch.a"),
        ),
    )
    now = datetime(2025, 1, 6, 3, tzinfo=TZ)
    check("cycle index", schedule.cycle_index(now) == 0)
    check("day in cycle", schedule.day_in_cycle(now) == 1)

    boundary = datetime(2025, 1, 14, 0, 0, tzinfo=TZ)
    check("cycle advances", schedule.cycle_index(boundary) == 1)
    check("day resets", schedule.day_in_cycle(boundary) == 1)

    before = datetime(2025, 1, 5, 12, tzinfo=TZ)
    check("negative cycle", schedule.cycle_index(before) == -1)
    check("day before anchor", schedule.day_in_cycle(before) == 8)

    upcoming = schedule.upcoming(datetime(2025, 1, 6, 6, 0, tzinfo=TZ), limit=2)
    check("upcoming ordered", [o.when.day for o in upcoming] == [7, 13])
    check("upcoming limit honored", len(upcoming) == 2)

    three = schedule.upcoming(datetime(2025, 1, 6, 3, tzinfo=TZ), limit=3)
    check("upcoming returns full limit", len(three) == 3, str(len(three)))

    last = schedule.last_before(datetime(2025, 1, 14, 1, tzinfo=TZ))
    check(
        "last_before",
        last is not None and last.when == datetime(2025, 1, 13, 23, tzinfo=TZ),
    )

    found = schedule.between(
        datetime(2025, 1, 6, 6, tzinfo=TZ), datetime(2025, 1, 8, 6, tzinfo=TZ)
    )
    check("between half open", [o.when.day for o in found] == [7], str(found))

    daily = Schedule(
        anchor_date=datetime(2025, 1, 6).date(),
        cycle_days=1,
        actions=(Action(1, time(7), "switch.turn_on"),),
    )
    run = daily.upcoming(datetime(2025, 1, 6, 8, tzinfo=TZ), limit=3)
    check("daily cycle", [o.when.date().day for o in run] == [7, 8, 9], str(run))

    ny = ZoneInfo("America/New_York")
    dst = Schedule(
        anchor_date=datetime(2026, 3, 7).date(),
        cycle_days=4,
        actions=(Action(2, time(6, 30), "switch.turn_on"),),
    )
    occ = dst.occurrence(0, dst.actions[0], ny)
    check("DST keeps wall clock", (occ.hour, occ.minute) == (6, 30))
    check("DST date", occ.date() == datetime(2026, 3, 8).date())

    try:
        Schedule(
            anchor_date=datetime(2025, 1, 6).date(),
            cycle_days=2,
            actions=(Action(3, time(6), "switch.turn_on"),),
        )
    except ScheduleError:
        check("action outside cycle rejected", True)
    else:
        check("action outside cycle rejected", False)


def test_config_parsing() -> None:
    print("config parsing")
    built = build_schedule(
        {
            "anchor_date": "2025-01-06",
            "cycle_days": 8,
            "rules": [
                {"day": 1, "time": "06:00", "service": "switch.turn_on"},
                {"day": 99, "time": "06:00", "service": "switch.turn_on"},
                {"day": 2, "time": "junk", "service": "switch.turn_on"},
                {"day": 3, "service": "switch.turn_on"},
                {"day": 4, "time": "07:00", "service": "switch.turn_on", "enabled": False},
            ],
        }
    )
    check("invalid rules skipped", len(built.actions) == 1, str(built.actions))

    # A fractional day would silently move an action to another date.
    fractional = build_schedule(
        {
            "anchor_date": "2025-01-06",
            "cycle_days": 8,
            "rules": [{"day": 1.9, "time": "06:00", "service": "switch.turn_on"}],
        }
    )
    check("fractional day rejected", len(fractional.actions) == 0, str(fractional.actions))

    # A full ISO timestamp is accepted for the anchor.
    stamped = build_schedule(
        {"anchor_date": "2025-01-06T00:00:00", "cycle_days": 8, "rules": []}
    )
    check("ISO timestamp anchor", stamped.anchor_date.isoformat() == "2025-01-06")

    for bad, label in (
        ({"cycle_days": 8, "rules": []}, "missing anchor"),
        ({"anchor_date": "2025-01-06", "cycle_days": 0, "rules": []}, "bad cycle"),
        ({"anchor_date": "2025-01-06", "cycle_days": 9999, "rules": []}, "cycle too long"),
        ({"anchor_date": "junk", "cycle_days": 8, "rules": []}, "bad anchor"),
    ):
        try:
            build_schedule(bad)
        except ConfigError:
            check(label, True)
        else:
            check(label, False)


# ---------------------------------------------------------------- live checks
async def boot(config_dir: pathlib.Path, calls: list[tuple[str, str]]):
    config_dir.mkdir(parents=True, exist_ok=True)
    dest = config_dir / "custom_components"
    if not dest.exists():
        shutil.copytree(
            PROJECT / "custom_components",
            dest,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    hass = core.HomeAssistant(str(config_dir))
    loader.async_setup(hass)
    hass.config.skip_pip = True
    assert await bootstrap.async_from_config_dict({"default_config": {}}, hass)

    async def fake(call):
        calls.append((call.service, str(call.data.get("entity_id"))))

    schema = vol.Schema({vol.Required("entity_id"): str}, extra=vol.ALLOW_EXTRA)
    for service in ("turn_on", "turn_off"):
        hass.services.async_register("switch", service, fake, schema=schema)
    return hass


async def start(hass) -> None:
    """Finish the boot sequence like the real runner does."""
    await hass.async_start()
    await hass.async_block_till_done()


def make_entry(name: str, now, rules: list[dict], cycle_days: int = 8) -> ConfigEntry:
    return ConfigEntry(
        created_at=now,
        data={},
        discovery_keys={},
        domain=DOMAIN,
        entry_id=name,
        minor_version=1,
        modified_at=now,
        options={
            "name": name,
            "anchor_date": now.date().isoformat(),
            "cycle_days": cycle_days,
            "rules": rules,
        },
        pref_disable_new_entities=False,
        pref_disable_polling=False,
        source="user",
        subentries_data=[],
        title=name,
        unique_id=name,
        version=1,
    )


async def test_timer_fires(calls) -> None:
    print("live timer")
    calls.clear()
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-timer-"))
    hass = await boot(config_dir, calls)
    now = dt_util.now()
    at = (now + timedelta(seconds=3)).strftime("%H:%M:%S")

    entry = make_entry(
        "live timer",
        now,
        [{"day": 1, "time": at, "service": "switch.turn_on", "entity_id": "switch.live"}],
        cycle_days=4,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    ok = await hass.config_entries.async_setup(entry.entry_id)
    check("entry loads", ok and entry.state.name == "LOADED", str(entry.state))
    await start(hass)

    sensor = hass.states.get("sensor.live_timer_next_run")
    check("sensor created", sensor is not None)

    await asyncio.sleep(6)
    await hass.async_block_till_done()

    check("service called", calls == [("turn_on", "switch.live")], str(calls))
    sensor = hass.states.get("sensor.live_timer_next_run")
    attrs = sensor.attributes
    check("run counted", attrs.get("run_count") == 1)
    check("last run recorded", attrs.get("last_run") is not None)
    fired = dt_util.parse_datetime(str(attrs.get("last_run")))
    nxt = dt_util.parse_datetime(str(sensor.state))
    check(
        "next run advanced",
        fired is not None and nxt is not None and nxt > fired,
        f"{sensor.state} vs {attrs.get('last_run')}",
    )
    check("no error", attrs.get("last_error") is None, str(attrs.get("last_error")))
    await hass.async_stop(force=True)


async def test_same_minute(calls) -> None:
    print("several actions in one minute")
    calls.clear()
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-multi-"))
    hass = await boot(config_dir, calls)
    now = dt_util.now()
    at = (now + timedelta(seconds=3)).strftime("%H:%M:%S")

    entry = make_entry(
        "multi",
        now,
        [
            {"day": 1, "time": at, "service": "switch.turn_on", "entity_id": "switch.a"},
            {"day": 1, "time": at, "service": "switch.turn_off", "entity_id": "switch.b"},
            {"day": 1, "time": at, "service": "switch.turn_on", "entity_id": "switch.c"},
        ],
        cycle_days=2,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await start(hass)
    await asyncio.sleep(6)
    await hass.async_block_till_done()

    check(
        "all three ran",
        [c[1] for c in calls] == ["switch.a", "switch.b", "switch.c"],
        str(calls),
    )
    await hass.async_stop(force=True)


async def test_disabled_and_custom(calls) -> None:
    print("disabled and custom rules")
    calls.clear()
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-custom-"))
    hass = await boot(config_dir, calls)
    now = dt_util.now()

    # A custom service call with extra data.
    async def custom_service(call):
        calls.append((call.domain + "." + call.service, str(call.data.get("message"))))

    hass.services.async_register("notify", "verify", custom_service)

    entry = make_entry(
        "custom",
        now,
        [
            {"day": 1, "time": "00:00:00", "service": "switch.turn_on",
             "entity_id": "switch.disabled", "enabled": False},
            {"day": 1, "time": "00:00:00", "service": "notify.verify",
             "data": {"message": "hello"}},
        ],
        cycle_days=2,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await start(hass)

    sensor = hass.states.get("sensor.custom_next_run")
    check(
        "disabled rule excluded",
        len(sensor.attributes.get("actions", [])) == 1,
        str(sensor.attributes.get("actions")),
    )

    # Run it manually through the service.
    await hass.services.async_call(
        DOMAIN,
        "run_action",
        {"entry_id": entry.entry_id, "rule_index": 0},
        blocking=True,
    )
    check("manual run", calls == [("notify.verify", "hello")], str(calls))
    await hass.async_stop(force=True)


async def test_config_flow() -> None:
    print("config flow")
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-flow-"))
    calls: list[tuple[str, str]] = []
    hass = await boot(config_dir, calls)
    today = dt_util.now().date().isoformat()

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    # First setup uses the "user" step (its own explanation); the options flow
    # uses "init" for the same form.
    check("flow starts", result["step_id"] == "user", str(result.get("step_id")))

    fid = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(
        fid, {"name": "Roster", "anchor_date": today, "cycle_days": 8}
    )
    check("menu shown", result["type"] == "menu", str(result.get("type")))
    check(
        "menu options",
        sorted(result["menu_options"])
        == ["add_action", "delete_action", "edit_action", "finish"],
        str(result.get("menu_options")),
    )

    result = await hass.config_entries.flow.async_configure(fid, {"next_step_id": "add_action"})
    check("rule step", result["step_id"] == "rule")

    result = await hass.config_entries.flow.async_configure(
        fid, {"day": 1, "time": "06:00:00", "service": "switch.turn_on"}
    )
    check("target step", result["step_id"] == "target")

    result = await hass.config_entries.flow.async_configure(fid, {"entity_id": "switch.demo"})
    check("back to menu", result["type"] == "menu")

    result = await hass.config_entries.flow.async_configure(fid, {"next_step_id": "finish"})
    check("entry created", result["type"] == "create_entry", str(result.get("type")))
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    options = dict(entry.options)
    check("stored in options", bool(options.get("rules")))
    check("day is int", isinstance(options["rules"][0]["day"], int),
          str(type(options["rules"][0]["day"])))

    # Options flow: add a second action.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    check("options init", result["step_id"] == "init")
    fid = result["flow_id"]
    result = await hass.config_entries.options.async_configure(
        fid, {"name": "Roster", "anchor_date": today, "cycle_days": 4}
    )
    check("options menu", result["type"] == "menu")
    result = await hass.config_entries.options.async_configure(fid, {"next_step_id": "add_action"})
    await hass.config_entries.options.async_configure(
        fid, {"day": 3, "time": "09:45:00", "service": "switch.turn_off"}
    )
    await hass.config_entries.options.async_configure(fid, {"entity_id": "switch.other"})
    result = await hass.config_entries.options.async_configure(fid, {"next_step_id": "finish"})
    check("options saved", result["type"] == "create_entry", str(result.get("type")))
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    rules = list(dict(entry.options)["rules"])
    check("second action added", len(rules) == 2, str(rules))
    check("cycle length updated", dict(entry.options)["cycle_days"] == 4)
    check("entry still loaded", entry.state.name == "LOADED", str(entry.state))

    # Deleting an action prunes it.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    fid = result["flow_id"]
    await hass.config_entries.options.async_configure(
        fid, {"name": "Roster", "anchor_date": today, "cycle_days": 4}
    )
    result = await hass.config_entries.options.async_configure(fid, {"next_step_id": "delete_action"})
    check("delete form", result["step_id"] == "confirm_delete", str(result.get("step_id")))
    await hass.config_entries.options.async_configure(fid, {"rule_index": "0"})
    await hass.config_entries.options.async_configure(fid, {"next_step_id": "finish"})
    await hass.async_block_till_done()
    rules = list(dict(hass.config_entries.async_entries(DOMAIN)[0].options)["rules"])
    check("action deleted", len(rules) == 1, str(rules))

    await hass.async_stop(force=True)


async def test_shrink_cycle_prunes() -> None:
    print("shrinking the cycle")
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-prune-"))
    calls: list[tuple[str, str]] = []
    hass = await boot(config_dir, calls)
    now = dt_util.now()
    entry = make_entry(
        "prune",
        now,
        [
            {"day": 1, "time": "06:00:00", "service": "switch.turn_on", "entity_id": "switch.a"},
            {"day": 8, "time": "06:00:00", "service": "switch.turn_off", "entity_id": "switch.a"},
        ],
        cycle_days=8,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await start(hass)

    today = now.date().isoformat()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    fid = result["flow_id"]
    result = await hass.config_entries.options.async_configure(
        fid, {"name": "prune", "anchor_date": today, "cycle_days": 2}
    )
    check("menu after shrink", result["type"] == "menu")
    await hass.config_entries.options.async_configure(fid, {"next_step_id": "finish"})
    await hass.async_block_till_done()

    rules = list(dict(hass.config_entries.async_entries(DOMAIN)[0].options)["rules"])
    check("out of range action pruned", len(rules) == 1, str(rules))
    check("entry reloaded", hass.config_entries.async_entries(DOMAIN)[0].state.name == "LOADED")
    await hass.async_stop(force=True)


async def test_restart_restore(calls) -> None:
    """Restart behaviour: no duplicates, but a missed action is caught up."""
    print("restart behaviour")
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-restart-")) / "cfg"

    # --- Part 1: an action fires, then a restart must not repeat it ---------
    calls.clear()
    hass = await boot(config_dir, calls)
    now = dt_util.now()
    at = (now + timedelta(seconds=3)).strftime("%H:%M:%S")
    entry = make_entry(
        "restart",
        now,
        [{"day": 1, "time": at, "service": "switch.turn_on", "entity_id": "switch.r"}],
        cycle_days=5,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await start(hass)
    await asyncio.sleep(6)
    await hass.async_block_till_done()
    check("action fired before restart", calls == [("turn_on", "switch.r")], str(calls))
    await hass.async_stop(force=True)

    calls.clear()
    hass = await boot(config_dir, calls)
    entries = hass.config_entries.async_entries(DOMAIN)
    check("entry persisted", len(entries) == 1, str(len(entries)))
    check("restored entry loaded", entries[0].state.name == "LOADED", str(entries[0].state))
    await start(hass)
    check("restart does not repeat", calls == [], str(calls))
    await hass.async_stop(force=True)

    # --- Part 2: an action passed while down -> converge once ---------------
    other = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-catchup-")) / "cfg"
    calls.clear()
    hass = await boot(other, calls)
    now = dt_util.now()
    past = now - timedelta(minutes=5)
    at = past.strftime("%H:%M:%S")

    entry = make_entry(
        "catchup",
        now,
        [{"day": 1, "time": at, "service": "switch.turn_off",
          "entity_id": "switch.c"}],
        cycle_days=5,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await start(hass)
    await hass.async_block_till_done()
    # Fresh install: the past action is skipped on purpose.
    check("install skips history", calls == [], str(calls))

    # Pretend a later occurrence exists but was never handled, i.e. Home
    # Assistant was down while it passed.
    scheduler = entry.runtime_data.scheduler
    scheduler._handled_for = past - timedelta(days=1)
    await scheduler._async_store()
    await hass.async_stop(force=True)
    await hass.async_block_till_done()

    calls.clear()
    hass = await boot(other, calls)
    await start(hass)
    await hass.async_block_till_done()
    check("missed action caught up", calls == [("turn_off", "switch.c")], str(calls))
    await hass.async_stop(force=True)


def test_domain_service_mapping() -> None:
    """Every offered domain must map to a service that really exists."""
    print("domain -> service mapping")
    from custom_components.staggered_switch.config_flow import (
        to_domain_service,
        to_generic_service,
    )
    from custom_components.staggered_switch.schedule import (
        TOGGLE_DOMAINS,
        TOGGLE_SERVICES,
    )

    expected = {
        "switch": ("switch.turn_on", "switch.turn_off"),
        "cover": ("cover.open_cover", "cover.close_cover"),
        "valve": ("valve.open_valve", "valve.close_valve"),
        "light": ("light.turn_on", "light.turn_off"),
    }
    for domain, (on_service, off_service) in expected.items():
        check(
            f"{domain} on service",
            to_domain_service("switch.turn_on", f"{domain}.x") == on_service,
            to_domain_service("switch.turn_on", f"{domain}.x"),
        )
        check(
            f"{domain} off service",
            to_domain_service("switch.turn_off", f"{domain}.x") == off_service,
            to_domain_service("switch.turn_off", f"{domain}.x"),
        )

    # Every picker domain must round trip back to the generic on/off choice.
    for domain in TOGGLE_DOMAINS:
        on_name, off_name = TOGGLE_SERVICES[domain]
        for generic, name in (
            ("switch.turn_on", on_name),
            ("switch.turn_off", off_name),
        ):
            back = to_generic_service(f"{domain}.{name}")
            check(f"{domain} round trip {name}", back == generic, back)

    # Button is not offered because it has no on/off pair.
    check("button not offered", "button" not in TOGGLE_DOMAINS, str(TOGGLE_DOMAINS))

    # Custom services are never rewritten.
    check(
        "custom service untouched",
        to_domain_service("notify.phone", "cover.x") == "notify.phone",
    )


async def test_service_validation() -> None:
    """run_action must reject nonsense instead of doing something surprising."""
    print("run_action validation")
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-svc-"))
    calls: list[tuple[str, str]] = []
    hass = await boot(config_dir, calls)
    now = dt_util.now()

    entry = make_entry(
        "svc",
        now,
        [
            {"day": 1, "time": "06:00:00", "service": "switch.turn_on",
             "entity_id": "switch.a"},
            {"day": 1, "time": "07:00:00", "service": "switch.turn_on",
             "entity_id": "switch.b"},
        ],
        cycle_days=3,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await start(hass)

    async def expect_error(label: str, data: dict) -> None:
        try:
            await hass.services.async_call(DOMAIN, "run_action", data, blocking=True)
        except Exception as err:  # noqa: BLE001
            check(label, True, type(err).__name__)
        else:
            check(label, False, "no error raised")

    await expect_error("missing entry id", {"rule_index": 0})
    await expect_error("unknown entry", {"entry_id": "nope", "rule_index": 0})
    await expect_error("index beyond end", {"entry_id": "svc", "rule_index": 99})
    await expect_error("negative index", {"entry_id": "svc", "rule_index": -1})
    await expect_error("non numeric index", {"entry_id": "svc", "rule_index": "abc"})

    calls.clear()
    await hass.services.async_call(
        DOMAIN, "run_action", {"entry_id": "svc", "rule_index": 0}, blocking=True
    )
    check("valid index runs", calls == [("turn_on", "switch.a")], str(calls))
    await hass.async_stop(force=True)


async def test_storage_and_failures() -> None:
    """Damaged state and unexpected service errors must not disable a cycle."""
    print("storage / failure isolation")
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-store-")) / "cfg"
    seen: list[tuple[str, str]] = []

    hass = await boot(config_dir, seen)
    now = dt_util.now()
    # Keep both actions inside the grace window, otherwise the scheduler
    # deliberately applies only the newest one (suspend protection).
    at = (now - timedelta(seconds=20)).strftime("%H:%M:%S")

    entry = make_entry(
        "store",
        now,
        [
            {"day": 1, "time": at, "service": "switch.turn_on", "entity_id": "switch.a"},
            {"day": 1, "time": at, "service": "switch.turn_off", "entity_id": "switch.b"},
        ],
        cycle_days=2,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await start(hass)

    scheduler = entry.runtime_data.scheduler

    # The first action raises something unexpected; the second must still run.
    async def boom(call):
        seen.append(("boom", str(call.data.get("entity_id"))))
        raise RuntimeError("unexpected")

    async def ok(call):
        seen.append(("ok", str(call.data.get("entity_id"))))

    hass.services.async_register("switch", "turn_on", boom)
    hass.services.async_register("switch", "turn_off", ok)

    seen.clear()
    scheduler._handled_for = now - timedelta(minutes=10)
    await scheduler._async_run_due(dt_util.now())

    check("both actions attempted", len(seen) == 2, str(seen))
    check("failure does not abort the batch", ("ok", "switch.b") in seen, str(seen))

    # A later success clears last_error, so verify the failing path on its own.
    seen.clear()
    scheduler.last_error = None
    scheduler._handled_for = now - timedelta(minutes=10)
    from custom_components.staggered_switch.schedule import Action, Occurrence

    failing = Occurrence(
        when=dt_util.now(),
        action=Action(1, time(6), "switch.turn_on", "switch.a"),
        cycle_index=0,
    )
    await scheduler._async_run(failing)
    check(
        "failure recorded",
        scheduler.last_error is not None and "unexpected" in scheduler.last_error,
        str(scheduler.last_error),
    )

    # A corrupted payload must not stop the entry from loading again.
    await scheduler._store.async_save(
        {"run_count": "not-a-number", "handled_for": "garbage"}
    )
    await hass.async_stop(force=True)
    await hass.async_block_till_done()

    seen.clear()
    hass = await boot(config_dir, seen)
    entries = hass.config_entries.async_entries(DOMAIN)
    check("entry reloads after corruption", len(entries) == 1, str(len(entries)))
    await start(hass)
    await hass.async_block_till_done()
    check(
        "cycle usable after corruption",
        entries[0].state.name == "LOADED",
        entries[0].state.name,
    )
    check(
        "sensor present after corruption",
        hass.states.get("sensor.store_next_run") is not None,
    )
    await hass.async_stop(force=True)


async def test_localisation() -> None:
    """Both languages must resolve, including the localised action labels."""
    print("localisation")
    today = dt_util.now().date().isoformat()

    for language, expected_next_step_word in (("en", "Next run"), ("zh-Hans", "下次执行时间")):
        config_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"ha-verify-i18n-{language}-"))
        calls: list[tuple[str, str]] = []
        hass = await boot(config_dir, calls)
        hass.config.language = language

        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        check(f"{language}: first step is user", r["step_id"] == "user", str(r.get("step_id")))
        fid = r["flow_id"]
        await hass.config_entries.flow.async_configure(
            fid, {"name": "Cycle", "anchor_date": today, "cycle_days": 4}
        )
        await hass.config_entries.flow.async_configure(fid, {"next_step_id": "add_action"})
        await hass.config_entries.flow.async_configure(
            fid, {"day": 1, "time": "06:00:00", "service": "switch.turn_on"}
        )
        r = await hass.config_entries.flow.async_configure(fid, {"entity_id": "switch.x"})
        summary = r.get("description_placeholders", {}).get("summary", "")
        check(f"{language}: summary resolved", bool(summary), repr(summary))
        check(
            f"{language}: summary mentions the entity",
            "switch.x" in summary,
            summary,
        )

        await hass.config_entries.flow.async_configure(fid, {"next_step_id": "finish"})
        await hass.async_block_till_done()

        state = hass.states.get("sensor.cycle_next_run")
        check(
            f"{language}: entity translated",
            state is not None and expected_next_step_word in state.name,
            state.name if state else None,
        )
        await hass.async_stop(force=True)


def test_leap_and_short_cycles() -> None:
    """Short cycles must not drift across a leap year."""
    print("leap year / cycle stability")
    schedule = Schedule(
        anchor_date=datetime(2027, 12, 20).date(),
        cycle_days=8,
        actions=(Action(1, time(6), "switch.turn_on"),),
    )
    starts = [schedule.cycle_start(i) for i in range(95)]
    gaps = {(b - a).days for a, b in zip(starts, starts[1:])}
    check("8 day stride constant", gaps == {8}, str(gaps))

    weekly = Schedule(
        anchor_date=datetime(2027, 12, 20).date(),
        cycle_days=7,
        actions=(Action(1, time(6), "switch.turn_on"),),
    )
    weekdays = {weekly.cycle_start(i).weekday() for i in range(200)}
    check("7 day cycle keeps weekday", weekdays == {datetime(2027, 12, 20).weekday()}, str(weekdays))

    # A 366 day cycle may shift by a day across leap years; that is expected,
    # but it must stay ordered and land at the configured clock time.
    long_cycle = Schedule(
        anchor_date=datetime(2027, 1, 1).date(),
        cycle_days=366,
        actions=(Action(60, time(6), "switch.turn_on"),),
    )
    occurrences = [long_cycle.occurrence(i, long_cycle.actions[0], None) for i in range(4)]
    check(
        "366 day occurrences ordered",
        all(a < b for a, b in zip(occurrences, occurrences[1:])),
        str([o.date() for o in occurrences]),
    )
    check("366 day keeps clock time", all(o.hour == 6 for o in occurrences))


def test_name_validation() -> None:
    """The cycle name becomes the entity id, so it must be sane."""
    print("name validation")
    from custom_components.staggered_switch.config_flow import (
        MAX_NAME_LENGTH,
        validate_name,
    )

    check("empty name rejected", validate_name("") is None)
    check("whitespace name rejected", validate_name("   ") is None)
    check("none rejected", validate_name(None) is None)
    check("whitespace collapsed", validate_name("  a   b  ") == "a b", str(validate_name("  a   b  ")))
    long_name = "x" * 200
    result = validate_name(long_name)
    check(
        "long name truncated",
        result is not None and len(result) == MAX_NAME_LENGTH,
        str(len(result) if result else None),
    )
    check("chinese name kept", validate_name("热水器排班") == "热水器排班")


def test_dst_gap_and_ordering() -> None:
    """Skipped local times must not collide with the following hour."""
    print("DST gap / absolute ordering")
    ny = ZoneInfo("America/New_York")

    # 02:30 does not exist on 2026-03-08; it must simply be dropped.
    gap = Schedule(
        anchor_date=datetime(2026, 3, 8).date(),
        cycle_days=2,
        actions=(Action(1, time(2, 30), "switch.turn_on"),),
    )
    check("gap occurrence is None", gap.occurrence(0, gap.actions[0], ny) is None)
    check("gap cycle has no occurrence", gap.occurrences_in_cycle(0, ny) == [])

    # A full day of quarter-hour slots must be strictly ordered, no duplicates.
    dense = Schedule(
        anchor_date=datetime(2026, 3, 8).date(),
        cycle_days=2,
        actions=tuple(
            Action(1, time(hour, minute), "switch.turn_on")
            for hour in range(6)
            for minute in (0, 15, 30, 45)
        ),
    )
    spring = dense.occurrences_in_cycle(0, ny)
    stamps = [item.when.timestamp() for item in spring]
    check("spring day ordered", all(a < b for a, b in zip(stamps, stamps[1:])))
    check("spring day no duplicates", len(set(stamps)) == len(stamps), f"{len(stamps)}")
    check("spring day drops skipped slots", len(spring) == 20, f"{len(spring)} of 24")

    fall = Schedule(
        anchor_date=datetime(2026, 11, 1).date(),
        cycle_days=2,
        actions=tuple(
            Action(1, time(hour, minute), "switch.turn_on")
            for hour in range(6)
            for minute in (0, 15, 30, 45)
        ),
    ).occurrences_in_cycle(0, ny)
    fall_stamps = [item.when.timestamp() for item in fall]
    check("fall day ordered", all(a < b for a, b in zip(fall_stamps, fall_stamps[1:])))
    check("fall day keeps all slots", len(fall) == 24, f"{len(fall)} of 24")

    # An ambiguous hour resolves to its first pass.
    ambiguous = Schedule(
        anchor_date=datetime(2026, 11, 1).date(),
        cycle_days=2,
        actions=(Action(1, time(1, 30), "switch.turn_on"),),
    ).occurrence(0, Action(1, time(1, 30), "switch.turn_on"), ny)
    check(
        "ambiguous hour uses earlier offset",
        ambiguous is not None and ambiguous.utcoffset().total_seconds() == -4 * 3600,
        ambiguous.isoformat() if ambiguous else "None",
    )

    # A zone without DST must be entirely unaffected.
    shanghai = ZoneInfo("Asia/Shanghai")
    plain = Schedule(
        anchor_date=datetime(2026, 3, 8).date(),
        cycle_days=2,
        actions=(Action(1, time(2, 30), "switch.turn_on"),),
    ).occurrence(0, Action(1, time(2, 30), "switch.turn_on"), shanghai)
    check(
        "no-DST zone unaffected",
        plain is not None and plain.strftime("%H:%M") == "02:30",
        plain.isoformat() if plain else "None",
    )


def test_payload_serialisation() -> None:
    """State payloads must be JSON safe, service calls must not be."""
    print("payload serialisation")
    import json

    from custom_components.staggered_switch.schedule import Action

    action = Action(
        day=1,
        at=time(6),
        service="notify.x",
        data={
            "when": datetime(2026, 1, 1, 12, 0),
            "raw": b"bytes",
            "set": {1, 2},
            "nested": {"list": (1, 2)},
            "n": 3,
            "flag": True,
            "none": None,
        },
    )
    payload = action.as_dict()
    try:
        json.dumps(payload)
        check("as_dict is JSON serialisable", True)
    except TypeError as err:
        check("as_dict is JSON serialisable", False, str(err))

    data = payload["data"]
    check("datetime converted to string", isinstance(data["when"], str), str(data["when"]))
    check("bytes decoded", data["raw"] == "bytes", str(data["raw"]))
    check("set becomes list", sorted(data["set"]) == [1, 2], str(data["set"]))
    check("nested list", data["nested"]["list"] == [1, 2], str(data["nested"]))
    check("int untouched", data["n"] == 3)
    check("bool untouched", data["flag"] is True)
    check("none untouched", data["none"] is None)

    # The real call must keep the original objects.
    served = action.service_data()
    check(
        "service_data keeps datetime",
        isinstance(served["when"], datetime),
        type(served["when"]).__name__,
    )
    check("service_data keeps set", isinstance(served["set"], set))


async def test_retarget_entity() -> None:
    """Changing the entity of an action must retarget its service."""
    print("retarget an action")
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-retarget-"))
    calls: list[tuple[str, str]] = []
    hass = await boot(config_dir, calls)
    today = dt_util.now().date().isoformat()

    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    fid = r["flow_id"]
    await hass.config_entries.flow.async_configure(
        fid, {"name": "Retarget", "anchor_date": today, "cycle_days": 5}
    )
    await hass.config_entries.flow.async_configure(fid, {"next_step_id": "add_action"})
    await hass.config_entries.flow.async_configure(
        fid, {"day": 1, "time": "06:00:00", "service": "switch.turn_on"}
    )
    await hass.config_entries.flow.async_configure(fid, {"entity_id": "switch.a"})
    await hass.config_entries.flow.async_configure(fid, {"next_step_id": "finish"})
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    rule = dict(entry.options)["rules"][0]
    check("initial target", rule["service"] == "switch.turn_on", str(rule))

    # switch -> cover, still "turn on"
    r = await hass.config_entries.options.async_init(entry.entry_id)
    fid = r["flow_id"]
    await hass.config_entries.options.async_configure(
        fid, {"name": "Retarget", "anchor_date": today, "cycle_days": 5}
    )
    await hass.config_entries.options.async_configure(fid, {"next_step_id": "edit_action"})
    await hass.config_entries.options.async_configure(fid, {"rule_index": "0"})
    await hass.config_entries.options.async_configure(
        fid, {"day": 1, "time": "06:00:00", "service": "switch.turn_on"}
    )
    await hass.config_entries.options.async_configure(fid, {"entity_id": "cover.x"})
    await hass.config_entries.options.async_configure(fid, {"next_step_id": "finish"})
    await hass.async_block_till_done()

    rule = dict(hass.config_entries.async_entries(DOMAIN)[0].options)["rules"][0]
    check("cover retargeted", rule["service"] == "cover.open_cover", str(rule))

    # cover -> light, now "turn off"
    r = await hass.config_entries.options.async_init(entry.entry_id)
    fid = r["flow_id"]
    await hass.config_entries.options.async_configure(
        fid, {"name": "Retarget", "anchor_date": today, "cycle_days": 5}
    )
    await hass.config_entries.options.async_configure(fid, {"next_step_id": "edit_action"})
    await hass.config_entries.options.async_configure(fid, {"rule_index": "0"})
    await hass.config_entries.options.async_configure(
        fid, {"day": 2, "time": "07:00:00", "service": "switch.turn_off"}
    )
    await hass.config_entries.options.async_configure(fid, {"entity_id": "light.y"})
    await hass.config_entries.options.async_configure(fid, {"next_step_id": "finish"})
    await hass.async_block_till_done()

    rule = dict(hass.config_entries.async_entries(DOMAIN)[0].options)["rules"][0]
    check("light retargeted", rule["service"] == "light.turn_off", str(rule))
    check("day updated", rule["day"] == 2, str(rule["day"]))
    await hass.async_stop(force=True)


async def test_integration_visibility_and_empty_cycle() -> None:
    """Regression: the entry must appear under Integrations, even with no actions.

    A ``helper`` integration_type made Home Assistant hide the entry from the
    integrations page (it was routed to the helpers UI, which only knows the
    core helper domains).  Saving with zero actions used to abort silently, so
    nothing was created at all.
    """
    print("integration visibility / empty cycle")
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-vis-"))
    calls: list[tuple[str, str]] = []
    hass = await boot(config_dir, calls)
    today = dt_util.now().date().isoformat()

    from homeassistant.loader import async_get_integrations

    integ = (await async_get_integrations(hass, [DOMAIN]))[DOMAIN]
    check("integration_type is service", integ.integration_type == "service",
          str(integ.integration_type))

    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    fid = r["flow_id"]
    await hass.config_entries.flow.async_configure(
        fid, {"name": "Empty Cycle", "anchor_date": today, "cycle_days": 8}
    )
    r = await hass.config_entries.flow.async_configure(fid, {"next_step_id": "finish"})
    check("saving without actions creates the entry",
          r["type"] == "create_entry", f"{r['type']} {r.get('reason')}")
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    check("entry exists", len(entries) == 1, str(len(entries)))
    if entries:
        entry = entries[0]
        check("actions empty", dict(entry.options).get("rules") == [])
        check("entry loaded", entry.state.name == "LOADED", entry.state.name)
        check("configure available", entry.supports_options is True)

        # The status sensor must exist (it is what shows up under Devices).
        check(
            "status sensor exists",
            hass.states.get("sensor.empty_cycle_next_run") is not None,
        )

        # And actions can be added afterwards.
        r = await hass.config_entries.options.async_init(entry.entry_id)
        fid = r["flow_id"]
        await hass.config_entries.options.async_configure(
            fid, {"name": "Empty Cycle", "anchor_date": today, "cycle_days": 8}
        )
        await hass.config_entries.options.async_configure(
            fid, {"next_step_id": "add_action"}
        )
        await hass.config_entries.options.async_configure(
            fid, {"day": 1, "time": "06:00:00", "service": "switch.turn_on"}
        )
        await hass.config_entries.options.async_configure(fid, {"entity_id": "switch.a"})
        r = await hass.config_entries.options.async_configure(
            fid, {"next_step_id": "finish"}
        )
        check("action can be added later", r["type"] == "create_entry", str(r.get("type")))
        await hass.async_block_till_done()
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        check("rule persisted", len(dict(entry.options)["rules"]) == 1,
              str(dict(entry.options)["rules"]))
    await hass.async_stop(force=True)


async def test_existing_install_editable() -> None:
    """An entry created by an older version must stay editable."""
    print("existing install stays editable")
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-old-"))
    calls: list[tuple[str, str]] = []
    hass = await boot(config_dir, calls)
    now = dt_util.now()

    entry = make_entry(
        "Old Cycle",
        now,
        [{"day": 1, "time": "06:00:00", "service": "switch.turn_on",
          "entity_id": "switch.a", "enabled": True}],
        cycle_days=8,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await start(hass)

    r = await hass.config_entries.options.async_init(entry.entry_id)
    check("options open", r["step_id"] == "init", str(r.get("step_id")))
    fid = r["flow_id"]
    r = await hass.config_entries.options.async_configure(
        fid, {"name": "Old Cycle", "anchor_date": now.date().isoformat(), "cycle_days": 8}
    )
    check("menu opens", r["type"] == "menu", str(r.get("type")))
    summary = (r.get("description_placeholders") or {}).get("summary", "")
    check("action visible in menu", "switch.a" in summary, summary)

    r = await hass.config_entries.options.async_configure(
        fid, {"next_step_id": "delete_action"}
    )
    check("delete picker", r["step_id"] == "confirm_delete", str(r.get("step_id")))
    await hass.config_entries.options.async_configure(fid, {"rule_index": "0"})
    await hass.config_entries.options.async_configure(fid, {"next_step_id": "finish"})
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    check("action deleted", dict(entry.options)["rules"] == [],
          str(dict(entry.options)["rules"]))
    await hass.async_stop(force=True)


async def test_custom_service_flow() -> None:
    """The custom-service branch must be reachable and validate its input."""
    print("custom service flow")
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-custflow-"))
    calls: list[tuple[str, str]] = []
    hass = await boot(config_dir, calls)
    today = dt_util.now().date().isoformat()

    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    fid = r["flow_id"]
    await hass.config_entries.flow.async_configure(
        fid, {"name": "Custom flow", "anchor_date": today, "cycle_days": 5}
    )
    await hass.config_entries.flow.async_configure(fid, {"next_step_id": "add_action"})
    r = await hass.config_entries.flow.async_configure(
        fid, {"day": 1, "time": "07:00:00", "service": "custom"}
    )
    check("custom form shown", r["step_id"] == "custom", str(r.get("step_id")))
    check(
        "custom form fields",
        set(r["data_schema"].schema) == {"service", "data"},
        str(set(r["data_schema"].schema)),
    )

    r = await hass.config_entries.flow.async_configure(
        fid, {"service": "not-a-service", "data": {}}
    )
    check(
        "invalid service rejected",
        r["type"] == "form" and r["errors"].get("base") == "invalid_service",
        str(r.get("errors")),
    )

    r = await hass.config_entries.flow.async_configure(
        fid, {"service": "notify.phone", "data": {"message": "hi", "n": 1}}
    )
    check("valid service accepted", r["type"] == "menu", str(r.get("type")))

    await hass.config_entries.flow.async_configure(fid, {"next_step_id": "finish"})
    await hass.async_block_till_done()

    rule = dict(hass.config_entries.async_entries(DOMAIN)[0].options)["rules"][0]
    check("service stored", rule["service"] == "notify.phone", str(rule))
    check(
        "data stored as dict",
        rule["data"] == {"message": "hi", "n": 1},
        str(rule["data"]),
    )
    check("no entity for custom", "entity_id" not in rule, str(rule))
    await hass.async_stop(force=True)


async def test_rename_cycle() -> None:
    """Renaming in the options flow updates the entry title too."""
    print("rename the cycle")
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-rename-"))
    calls: list[tuple[str, str]] = []
    hass = await boot(config_dir, calls)
    today = dt_util.now().date().isoformat()

    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    fid = r["flow_id"]
    await hass.config_entries.flow.async_configure(
        fid, {"name": "Original", "anchor_date": today, "cycle_days": 5}
    )
    await hass.config_entries.flow.async_configure(fid, {"next_step_id": "add_action"})
    await hass.config_entries.flow.async_configure(
        fid, {"day": 1, "time": "07:00:00", "service": "switch.turn_on"}
    )
    await hass.config_entries.flow.async_configure(fid, {"entity_id": "switch.x"})
    await hass.config_entries.flow.async_configure(fid, {"next_step_id": "finish"})
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    check("original title", entry.title == "Original", entry.title)

    r = await hass.config_entries.options.async_init(entry.entry_id)
    fid = r["flow_id"]
    await hass.config_entries.options.async_configure(
        fid, {"name": "Renamed", "anchor_date": today, "cycle_days": 5}
    )
    await hass.config_entries.options.async_configure(fid, {"next_step_id": "finish"})
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    check("title renamed", entry.title == "Renamed", entry.title)
    check(
        "options name renamed",
        dict(entry.options)["name"] == "Renamed",
        str(dict(entry.options).get("name")),
    )
    await hass.async_stop(force=True)


async def test_edit_action_fields() -> None:
    """Editing an action rewrites every field, not just appends."""
    print("edit an action")
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-edit-"))
    calls: list[tuple[str, str]] = []
    hass = await boot(config_dir, calls)
    today = dt_util.now().date().isoformat()

    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    fid = r["flow_id"]
    await hass.config_entries.flow.async_configure(
        fid, {"name": "Edit", "anchor_date": today, "cycle_days": 10}
    )
    for day, tm, svc, ent in (
        (1, "06:00:00", "switch.turn_on", "switch.a"),
        (5, "22:00:00", "switch.turn_off", "switch.b"),
    ):
        await hass.config_entries.flow.async_configure(fid, {"next_step_id": "add_action"})
        await hass.config_entries.flow.async_configure(
            fid, {"day": day, "time": tm, "service": svc}
        )
        await hass.config_entries.flow.async_configure(fid, {"entity_id": ent})

    r = await hass.config_entries.flow.async_configure(fid, {"next_step_id": "edit_action"})
    check("edit picker shown", r["step_id"] == "pick", str(r.get("step_id")))
    r = await hass.config_entries.flow.async_configure(fid, {"rule_index": "0"})
    check("edit prefilled", r["step_id"] == "rule", str(r.get("step_id")))
    r = await hass.config_entries.flow.async_configure(
        fid, {"day": 3, "time": "07:30:00", "service": "switch.turn_on"}
    )
    await hass.config_entries.flow.async_configure(fid, {"entity_id": "switch.c"})
    await hass.config_entries.flow.async_configure(fid, {"next_step_id": "finish"})
    await hass.async_block_till_done()

    rules = list(dict(hass.config_entries.async_entries(DOMAIN)[0].options)["rules"])
    check("edit replaced the rule", len(rules) == 2, str(rules))
    check(
        "edited fields applied",
        rules[0]
        == {
            "day": 3,
            "time": "07:30:00",
            "service": "switch.turn_on",
            "entity_id": "switch.c",
            "enabled": True,
        },
        str(rules[0]),
    )
    check("other rule untouched", rules[1]["day"] == 5, str(rules[1]))
    await hass.async_stop(force=True)


async def test_reload_keeps_state() -> None:
    """Changing options while running must not replay anything."""
    print("reload after options change")
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-reload-"))
    local: list[tuple[str, str]] = []
    hass = await boot(config_dir, local)
    now = dt_util.now()
    at = (now - timedelta(hours=2)).strftime("%H:%M:%S")
    entry = make_entry(
        "reload",
        now,
        [{"day": 1, "time": at, "service": "switch.turn_on", "entity_id": "switch.r"}],
        cycle_days=8,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await start(hass)
    check("nothing at boot", local == [], str(local))

    hass.config_entries.async_update_entry(
        entry, options={**dict(entry.options), "cycle_days": 4}
    )
    await hass.async_block_till_done()
    await asyncio.sleep(1)
    await hass.async_block_till_done()

    check("no replay on reload", local == [], str(local))
    sensor = hass.states.get("sensor.reload_next_run")
    check(
        "cycle length applied",
        sensor is not None and sensor.attributes.get("cycle_days") == 4,
        str(sensor.attributes.get("cycle_days") if sensor else None),
    )
    await hass.async_stop(force=True)


async def test_suspend_catch_up(calls) -> None:
    """A late timer must converge instead of replaying stale actions."""
    print("suspend / late timer")
    calls.clear()
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-suspend-"))
    hass = await boot(config_dir, calls)
    now = dt_util.now()

    # Two actions a minute apart, both already in the past.
    first = (now - timedelta(minutes=40)).strftime("%H:%M:%S")
    second = (now - timedelta(minutes=30)).strftime("%H:%M:%S")

    entry = make_entry(
        "suspend",
        now,
        [
            {"day": 1, "time": first, "service": "switch.turn_on",
             "entity_id": "switch.s1"},
            {"day": 1, "time": second, "service": "switch.turn_off",
             "entity_id": "switch.s2"},
        ],
        cycle_days=3,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await start(hass)
    scheduler = entry.runtime_data.scheduler
    check("fresh install stays silent", calls == [], str(calls))

    pending = scheduler.schedule.between(
        now - timedelta(hours=1), now
    )
    check("pending actions found", len(pending) == 2, str(len(pending)))

    # Pretend both became due while the machine was asleep and the timer fired
    # only now: the late path must apply just the newest one.
    scheduler._handled_for = now - timedelta(hours=1)
    await scheduler._async_run_due(now)
    check(
        "late timer applies only newest",
        [c[1] for c in calls] == ["switch.s2"],
        str(calls),
    )
    await hass.async_stop(force=True)


async def test_unload() -> None:
    print("unload")
    config_dir = pathlib.Path(tempfile.mkdtemp(prefix="ha-verify-unload-"))
    calls: list[tuple[str, str]] = []
    hass = await boot(config_dir, calls)
    now = dt_util.now()
    entry = make_entry(
        "unload",
        now,
        [{"day": 1, "time": "06:00:00", "service": "switch.turn_on", "entity_id": "switch.u"}],
        cycle_days=4,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await start(hass)
    ok = await hass.config_entries.async_unload(entry.entry_id)
    check("unload returns true", ok)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.unload_next_run")
    check(
        "entity unavailable after unload",
        state is None or state.state == "unavailable",
        f"{state.state if state else None}",
    )
    check("entry unloaded", entry.state.name == "NOT_LOADED", str(entry.state))
    await hass.async_stop(force=True)


async def main() -> None:
    test_schedule_logic()
    test_config_parsing()
    calls: list[tuple[str, str]] = []
    await test_timer_fires(calls)
    await test_same_minute(calls)
    await test_disabled_and_custom(calls)
    await test_config_flow()
    await test_shrink_cycle_prunes()
    await test_restart_restore(calls)
    await test_suspend_catch_up(calls)
    test_leap_and_short_cycles()
    test_domain_service_mapping()
    test_name_validation()
    test_dst_gap_and_ordering()
    test_payload_serialisation()
    await test_integration_visibility_and_empty_cycle()
    await test_existing_install_editable()
    await test_retarget_entity()
    await test_service_validation()
    await test_storage_and_failures()
    await test_localisation()
    await test_custom_service_flow()
    await test_rename_cycle()
    await test_edit_action_fields()
    await test_reload_keeps_state()
    await test_unload()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):", FAILURES)
        sys.exit(1)
    print("all checks passed")


asyncio.run(main())
