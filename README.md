# Staggered Switch

A Home Assistant custom integration that switches an entity on or off according
to a repeating **multi-day cycle**. Instead of writing one automation per shift,
you describe the roster once — "day 3 of the cycle at 06:30 → turn on" — and the
cycle repeats forever.

It is designed for schedules that do not line up with weeks: 8-day rotations,
12-hour shift patterns, alternating duty rosters, or any "every N days" plan.

[![简体中文](https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-0969da?style=for-the-badge&logo=readme&logoColor=white)](README.zh-CN.md)

> **Note** — this is a community integration, not an official Home Assistant
> integration. Home Assistant will show the usual "not tested by Home Assistant"
> warning.

## Features

- **Cycle based scheduling** — pick any cycle length from 1 to 366 days and set
  the first day; each action is addressed as *day N of the cycle* plus a clock
  time.
- **Any number of actions** — each day can carry several actions, and several
  actions may share the exact same minute.
- **Turn on / turn off / custom** — use the switch-style helpers, or call any
  Home Assistant service (for example a notification) with your own data.
- **Survives restarts** — if the device lost power, the newest action is
  re-applied once on start-up so the entity matches the roster again. Installing
  the integration never switches anything by itself.
- **Daylight saving safe** — actions keep their local wall clock time when the
  offset changes. A time that does not exist that day (for example 02:30 when
  the clock jumps from 02:00 to 03:00) is skipped once rather than piled onto
  the following hour; an hour that happens twice uses the first pass.
- **Editable later** — reopen the integration options to add, edit, delete,
  disable actions, or change the cycle length; actions that no longer fit are
  pruned automatically.
- **Status sensor** — exposes the current cycle position, the next run, the last
  run, and any error from the last service call.

## Installation

### HACS (custom repository)

1. In HACS, open the menu and choose **Custom repositories**.
2. Add the URL of this repository and select the **Integration** category.
3. Install **Staggered Switch**.
4. Restart Home Assistant.

### Manual

Copy the `custom_components/staggered_switch` folder into your Home Assistant
`config/custom_components` directory and restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & services → Add integration**.
2. Search for **Staggered Switch**.
3. Fill in:

   | Field | Meaning |
   | --- | --- |
   | Name | Display name for this cycle. |
   | First day of the cycle | Day 1 of the cycle. |
   | Cycle length (days) | How many days one full cycle lasts. |

4. Add actions from the menu. Everything about one action is collected on a
   **single page**:

   | Field | Meaning |
   | --- | --- |
   | Day of the cycle | 1 … cycle length |
   | Time of day | Local wall clock time |
   | Action | Turn on, turn off, or another service call |
   | Entity | Which entity to switch (required for turn on / turn off) |

   Pick *Another service call* to call an arbitrary service; the entity field is
   optional in that case and is passed along when you fill it in.

5. Repeat **Add an action** as needed, then **Save and finish**.

The action picker offers **Turn on**, **Turn off** and **Another service call**.
The actual service is resolved from the entity you choose, because domains do
not agree on naming: a `cover` gets `open_cover` / `close_cover` and a `valve`
gets `open_valve` / `close_valve`, while switches and lights use
`turn_on` / `turn_off`.

Supported domains: `switch`, `light`, `fan`, `input_boolean`, `automation`,
`humidifier`, `media_player`, `remote`, `siren`, `cover`, `valve`. Buttons only
support `press`, so use **Another service call** if you need one.

Everything is editable later through the integration's **Configure** button.

### Example: an 8-day rotation

Cycle length `8`, first day `2024-01-01` (day 1 of the cycle):

| Day | Time | Action |
| --- | --- | --- |
| 1 | 06:00 | Turn on `switch.water_heater` |
| 2 | 06:00 | Turn on `switch.water_heater` |
| 3 | 06:00 | Turn on `switch.water_heater` |
| 4 | 06:00 | Turn on `switch.water_heater` |
| 8 | 23:00 | Turn off `switch.water_heater` |

Day 9 is the first day of the next cycle again, and so on.

## Entities

Each cycle creates one sensor, named **Next run**, whose state is the timestamp
of the next action. Its attributes include:

| Attribute | Meaning |
| --- | --- |
| `cycle_index` | Number of the current cycle (0 = the first one). |
| `cycle_day` | Current day inside the cycle (1-based). |
| `cycle_days` | Configured cycle length. |
| `cycle_start` / `cycle_end` | First and last day of the current cycle. |
| `next_run` / `next_action` | The next scheduled instant and what it will do. |
| `upcoming` | The next three actions. |
| `last_run` / `last_action` | The last successful execution. |
| `last_error` | The error of the last call, if it failed. |
| `run_count` | How many actions ran since the integration was added. |
| `actions` | Configured actions in stored order (first 50). |
| `actions_total` | Total number of configured actions. |

## Service

### `staggered_switch.run_action`

Run one configured action immediately, ignoring its scheduled time. This is
handy for testing a schedule.

| Field | Meaning |
| --- | --- |
| `entry_id` | The cycle that owns the action. |
| `rule_index` | Zero-based position of the action, as listed by the sensor's `actions` attribute. |

```yaml
service: staggered_switch.run_action
data:
  entry_id: 01J8Z3Q9VK4M2P7T1H6B5N0CXD
  rule_index: 0
```

## How the cycle is evaluated

- Day 1 of a cycle is the configured first day. A cycle of *N* days therefore
  covers days 1 … N, then repeats.
- Each day is a full calendar day; an action at 00:30 creates a fresh time, it
  does not inherit anything from the previous day.
- The **next run** is always strictly in the future, so an action that already
  fired is never reported again.
- Actions are executed in time order. If two actions share a minute, both run.
- If an action fails (for example the entity is unavailable), the error is
  exposed in the sensor and the schedule keeps going.

## Troubleshooting

- **Nothing switches.** Check the sensor: is `next_run` in the future? Compare
  `cycle_day` with the configured day numbers. Remember that day 1 is the first
  day you configured, not "today".
- **A device switched right after a restart.** That is the start-up
  convergence: the newest action is re-applied so the entity matches the
  roster. It happens at most once per action.
- **One action failed but others still ran.** Failures are isolated per action;
  the error is shown in `last_error` and the schedule keeps working.
- **`last_error` is set.** The target entity or service was unavailable when
  the action fired. Check the entity id and that the integration providing it
  is working.
- **Nothing ran on the changeover day.** If an action is scheduled inside the
  hour that a DST change skips, it does not run that day. Pick a time outside
  the skipped hour, or add a second action at a safe time.

## License

MIT
