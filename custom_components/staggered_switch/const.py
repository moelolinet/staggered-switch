"""Constants for the Staggered Switch integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "staggered_switch"

PLATFORMS: Final = ["sensor"]

# Config entry keys
CONF_NAME: Final = "name"
CONF_ANCHOR_DATE: Final = "anchor_date"
CONF_CYCLE_DAYS: Final = "cycle_days"
CONF_RULES: Final = "rules"

# Rule keys
CONF_RULE_ENABLED: Final = "enabled"
CONF_RULE_DAY: Final = "day"
CONF_RULE_TIME: Final = "time"
CONF_RULE_SERVICE: Final = "service"
CONF_RULE_ENTITY: Final = "entity_id"
CONF_RULE_DATA: Final = "data"

# Internal option used by the "pick a rule" step
CONF_RULE_INDEX: Final = "rule_index"

MIN_CYCLE_DAYS: Final = 1
MAX_CYCLE_DAYS: Final = 366
MAX_RULES: Final = 200

# A timer can fire slightly late; anything due within this many seconds is
# still executed instead of being skipped.
DUE_GRACE: Final = 90

# Attributes exposed by the status sensor
ATTR_CYCLE_INDEX: Final = "cycle_index"
ATTR_CYCLE_DAY: Final = "cycle_day"
ATTR_CYCLE_DAYS: Final = "cycle_days"
ATTR_CYCLE_START: Final = "cycle_start"
ATTR_CYCLE_END: Final = "cycle_end"
ATTR_NEXT_RUN: Final = "next_run"
ATTR_NEXT_ACTION: Final = "next_action"
ATTR_UPCOMING: Final = "upcoming"
ATTR_LAST_RUN: Final = "last_run"
ATTR_LAST_ACTION: Final = "last_action"
ATTR_LAST_ERROR: Final = "last_error"
ATTR_RUN_COUNT: Final = "run_count"
ATTR_ACTIONS: Final = "actions"
ATTR_ACTIONS_TOTAL: Final = "actions_total"
