"""Config and options flow for Staggered Switch.

The cycle definition (name, anchor date, length) is collected first, then a menu
lets the user add, edit and delete individual actions.  The same steps are used
by the options flow so editing later behaves exactly like the first setup.

Menu entries use fixed keys so their labels come from ``strings.json``; the
action to edit or delete is chosen from a dropdown with readable labels.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector, translation
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ANCHOR_DATE,
    CONF_CYCLE_DAYS,
    CONF_RULE_DATA,
    CONF_RULE_DAY,
    CONF_RULE_ENABLED,
    CONF_RULE_ENTITY,
    CONF_RULE_INDEX,
    CONF_RULE_SERVICE,
    CONF_RULE_TIME,
    CONF_RULES,
    DOMAIN,
    MAX_CYCLE_DAYS,
    MAX_RULES,
    MIN_CYCLE_DAYS,
)
from .schedule import TOGGLE_DOMAINS, TOGGLE_SERVICES, toggle_service

_LOGGER = logging.getLogger(__name__)

CUSTOM_SERVICE = "custom"

SERVICE_OPTIONS = [
    {"value": "switch.turn_on", "label": "Turn on"},
    {"value": "switch.turn_off", "label": "Turn off"},
    {"value": CUSTOM_SERVICE, "label": "Another service call"},
]

MENU_OPTIONS = ["add_action", "edit_action", "delete_action", "finish"]


# ---------------------------------------------------------------- schemas
DEFAULT_NAME = "Staggered switch"

MAX_NAME_LENGTH = 64


def validate_name(value: Any) -> str | None:
    """Return a usable cycle name, or ``None`` when the input is unusable.

    The name becomes the config entry title, the device name and therefore the
    generated entity id: an empty name would produce a bare ``sensor.next_run``
    that collides with other integrations, and a very long one produces an
    entity id that is painful to use in automations.
    """
    if value is None:
        return None
    name = " ".join(str(value).split())
    if not name:
        return None
    return name[:MAX_NAME_LENGTH]


def schedule_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Return the form that defines the cycle itself."""
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.TEXT, autocomplete="off"
                )
            ),
            vol.Required(
                CONF_ANCHOR_DATE,
                default=defaults.get(
                    CONF_ANCHOR_DATE, dt_util.now().date().isoformat()
                ),
            ): selector.DateSelector(),
            vol.Required(
                CONF_CYCLE_DAYS, default=int(defaults.get(CONF_CYCLE_DAYS, 8))
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_CYCLE_DAYS,
                    max=MAX_CYCLE_DAYS,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="days",
                )
            ),
        }
    )


def action_schema(cycle_days: int, defaults: Mapping[str, Any]) -> vol.Schema:
    """Return the single form that describes one action.

    Day, time, what to do and which entity are collected together: splitting
    them over two pages made the flow feel unfinished, and the entity field is
    only meaningful for the turn on / turn off choices anyway, so it is
    optional here and ignored for a custom service call.
    """
    day = min(max(int(defaults.get(CONF_RULE_DAY, 1)), 1), cycle_days)
    entity = defaults.get(CONF_RULE_ENTITY)

    entity_key = (
        vol.Optional(CONF_RULE_ENTITY, default=entity)
        if entity
        else vol.Optional(CONF_RULE_ENTITY)
    )

    return vol.Schema(
        {
            vol.Required(CONF_RULE_DAY, default=day): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=cycle_days, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_RULE_TIME, default=defaults.get(CONF_RULE_TIME, "06:00:00")
            ): selector.TimeSelector(),
            vol.Required(
                CONF_RULE_SERVICE,
                default=to_generic_service(
                    str(defaults.get(CONF_RULE_SERVICE, GENERIC_ON))
                ),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=SERVICE_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="service",
                )
            ),
            entity_key: selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=list(TOGGLE_DOMAINS), multiple=False
                )
            ),
        }
    )


def custom_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Return the raw service call form."""
    return vol.Schema(
        {
            vol.Required(
                CONF_RULE_SERVICE, default=defaults.get(CONF_RULE_SERVICE, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Optional(
                CONF_RULE_DATA, default=dict(defaults.get(CONF_RULE_DATA) or {})
            ): selector.ObjectSelector(),
        }
    )


GENERIC_ON = "switch.turn_on"
GENERIC_OFF = "switch.turn_off"

# Every "on-like" / "off-like" service the picker can produce.
_ON_SERVICES = {
    f"{domain}.{on}"
    for domain, (on, _off) in TOGGLE_SERVICES.items()
} | {GENERIC_ON, "turn_on"}
_OFF_SERVICES = {
    f"{domain}.{off}"
    for domain, (_on, off) in TOGGLE_SERVICES.items()
} | {GENERIC_OFF, "turn_off"}


def to_generic_service(service: str) -> str:
    """Map a stored service back to the on/off choice shown in the form."""
    if service in _ON_SERVICES:
        return GENERIC_ON
    if service in _OFF_SERVICES:
        return GENERIC_OFF
    return service


def to_domain_service(service: str, entity_id: str | None) -> str:
    """Translate the generic on/off choice into the entity's own service.

    Covers and valves use ``open_*`` / ``close_*``, buttons only ``press``;
    calling ``cover.turn_on`` would fail, so the service is resolved from the
    selected entity instead of being stored as a guess.
    """
    if not entity_id or not service.startswith("switch."):
        return service

    domain = entity_id.split(".", 1)[0]
    if service == GENERIC_ON:
        return toggle_service(domain, turn_on=True)
    if service == GENERIC_OFF:
        return toggle_service(domain, turn_on=False)
    return service


def stored_rules(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the actions of a payload as a fresh list."""
    rules = payload.get(CONF_RULES) or []
    if not isinstance(rules, list):
        return []
    return [dict(rule) for rule in rules if isinstance(rule, Mapping)]


TRANSLATION_KEY = "component.staggered_switch.config.step.menu.rule_summary"


async def async_rule_summary_strings(hass) -> dict[str, str]:
    """Return the translated templates used to describe actions.

    Falls back to English when translations are unavailable, so a missing
    translation file never breaks the flow.
    """
    fallback = {
        "rule_label": "Day {day} at {time} - {service}",
        "rule_label_target": "Day {day} at {time} - {service} ({entity})",
        "rule_disabled": "{label} (disabled)",
        "no_actions": "No actions yet - add at least one.",
        "action_on": "turn on",
        "action_off": "turn off",
        "warning_no_actions": (
            "\n\n**This cycle has no actions yet**, so nothing will run. "
            "Add at least one action below."
        ),
    }
    try:
        translations = await translation.async_get_translations(
            hass,
            hass.config.language,
            "config",
            integrations=[DOMAIN],
            config_flow=True,
        )
    except Exception:  # noqa: BLE001 - never break the flow over wording
        return fallback

    return {
        key: translations.get(f"{TRANSLATION_KEY}.{key}", fallback[key])
        for key in fallback
    }


def describe(rule: Mapping[str, Any], strings: Mapping[str, str] | None = None) -> str:
    """Return a readable one line description of an action."""
    strings = strings or {}
    day = rule.get(CONF_RULE_DAY, "?")
    at = str(rule.get(CONF_RULE_TIME, ""))[:5]
    service = rule.get(CONF_RULE_SERVICE, "?")

    # Present the two built-in actions in words instead of raw service ids.
    service = {
        "switch.turn_on": strings.get("action_on", "turn on"),
        "switch.turn_off": strings.get("action_off", "turn off"),
    }.get(str(service), str(service))

    target = rule.get(CONF_RULE_ENTITY)

    if target:
        template = strings.get(
            "rule_label_target", "Day {day} at {time} - {service} ({entity})"
        )
        return template.format(day=day, time=at, service=service, entity=target)

    template = strings.get("rule_label", "Day {day} at {time} - {service}")
    return template.format(day=day, time=at, service=service)


def rule_picker(
    rules: list[dict[str, Any]], strings: Mapping[str, str] | None = None
) -> selector.SelectSelector:
    """Return a dropdown listing the configured actions."""
    strings = strings or {}
    disabled = strings.get("rule_disabled", "{label} (disabled)")
    options = []
    for index, rule in enumerate(rules):
        label = describe(rule, strings)
        if not rule.get(CONF_RULE_ENABLED, True):
            label = disabled.format(label=label)
        options.append({"value": str(index), "label": label})

    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options, mode=selector.SelectSelectorMode.LIST
        )
    )


# ------------------------------------------------------------ steps mixin
class RuleSteps:
    """All steps except the two entry points.

    A real mixin (no Home Assistant flow base class) so both flows can inherit
    the exact same behaviour without duplicating code.
    """

    _working: dict[str, Any]
    _draft: dict[str, Any]
    _edit_index: int | None

    # -- helpers ---------------------------------------------------------
    async def _summary(self, rules: list[dict[str, Any]]) -> str:
        """Return a markdown summary of the configured actions."""
        strings = await async_rule_summary_strings(self.hass)
        if not rules:
            return strings["no_actions"]
        return "\n".join(f"- {describe(rule, strings)}" for rule in rules)

    def _prune_out_of_range(self) -> None:
        """Drop actions whose day no longer exists after shortening a cycle."""
        cycle_days = int(self._working[CONF_CYCLE_DAYS])
        rules = [
            rule
            for rule in stored_rules(self._working)
            if 1 <= int(rule.get(CONF_RULE_DAY, 1)) <= cycle_days
        ]
        self._working = {**self._working, CONF_RULES: rules}

    # -- cycle definition -------------------------------------------------
    async def _async_cycle_form(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Render the cycle definition form under the given step id."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = validate_name(user_input.get(CONF_NAME))
            if name is None:
                errors["base"] = "invalid_name"
            else:
                self._working = {
                    **self._working,
                    CONF_NAME: name,
                    CONF_ANCHOR_DATE: user_input[CONF_ANCHOR_DATE],
                    CONF_CYCLE_DAYS: int(user_input[CONF_CYCLE_DAYS]),
                }
                self._prune_out_of_range()
                return await self.async_step_menu()

        return self.async_show_form(
            step_id=step_id,
            data_schema=schedule_schema(
                {**self._working, CONF_NAME: (user_input or {}).get(CONF_NAME)}
                if errors
                else self._working
            ),
            errors=errors,
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Cycle definition, shown by the options flow."""
        return await self._async_cycle_form("init", user_input)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Cycle definition, shown during first setup."""
        return await self._async_cycle_form("user", user_input)

    # -- menu -------------------------------------------------------------
    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the action management menu."""
        rules = stored_rules(self._working)
        strings = await async_rule_summary_strings(self.hass)
        return self.async_show_menu(
            step_id="menu",
            menu_options=MENU_OPTIONS,
            description_placeholders={
                "cycle_days": str(self._working[CONF_CYCLE_DAYS]),
                "count": str(len(rules)),
                "summary": await self._summary(rules),
                "warning": (
                    strings["warning_no_actions"] if not rules else ""
                ),
            },
        )

    async def async_step_add_action(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start a brand new action."""
        self._draft = {}
        self._edit_index = None
        return await self.async_step_rule()

    async def async_step_edit_action(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an existing action to edit."""
        return await self.async_step_pick()

    async def async_step_delete_action(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an existing action to delete."""
        return await self.async_step_confirm_delete()

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Store the finished payload."""
        # Saving is always allowed: an empty cycle still creates the entry so
        # it can be configured later.  The menu already explains the situation.
        return self._store_options()

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an existing action to edit."""
        rules = stored_rules(self._working)
        if not rules:
            return self.async_abort(reason="no_rules")

        if user_input is not None:
            self._edit_index = int(user_input[CONF_RULE_INDEX])
            self._draft = dict(rules[self._edit_index])
            # Show the generic on/off choice, not the domain specific service.
            self._draft[CONF_RULE_SERVICE] = to_generic_service(
                str(self._draft.get(CONF_RULE_SERVICE, GENERIC_ON))
            )
            return await self.async_step_rule()

        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_RULE_INDEX): rule_picker(
                        rules, await async_rule_summary_strings(self.hass)
                    )
                }
            ),
        )

    async def async_step_confirm_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an existing action and delete it."""
        rules = stored_rules(self._working)
        if not rules:
            return self.async_abort(reason="no_rules")

        if user_input is not None:
            rules.pop(int(user_input[CONF_RULE_INDEX]))
            self._working = {**self._working, CONF_RULES: rules}
            return await self.async_step_menu()

        return self.async_show_form(
            step_id="confirm_delete",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_RULE_INDEX): rule_picker(
                        rules, await async_rule_summary_strings(self.hass)
                    )
                }
            ),
        )

    # -- action details ---------------------------------------------------
    async def async_step_rule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect day, time, action and entity on a single page."""
        cycle_days = int(self._working[CONF_CYCLE_DAYS])

        if user_input is not None:
            service = str(user_input[CONF_RULE_SERVICE])
            entity_id = user_input.get(CONF_RULE_ENTITY) or None
            errors: dict[str, str] = {}

            if service == CUSTOM_SERVICE:
                # A custom call may still target the chosen entity, but it is
                # not required; continue to the custom-service page.
                self._draft = {
                    **self._draft,
                    **user_input,
                    CONF_RULE_DAY: int(user_input[CONF_RULE_DAY]),
                }
                if entity_id:
                    self._draft[CONF_RULE_ENTITY] = entity_id
                return await self.async_step_custom()

            if not entity_id:
                errors["base"] = "entity_required"
            else:
                draft = {
                    **self._draft,
                    **user_input,
                    CONF_RULE_DAY: int(user_input[CONF_RULE_DAY]),
                    CONF_RULE_ENTITY: entity_id,
                    CONF_RULE_SERVICE: to_domain_service(service, entity_id),
                    CONF_RULE_ENABLED: True,
                }
                draft.pop(CONF_RULE_DATA, None)
                return await self._store_rule(draft)

            return self.async_show_form(
                step_id="rule",
                data_schema=action_schema(cycle_days, {**self._draft, **user_input}),
                description_placeholders={"cycle_days": str(cycle_days)},
                errors=errors,
            )

        return self.async_show_form(
            step_id="rule",
            data_schema=action_schema(cycle_days, self._draft),
            description_placeholders={"cycle_days": str(cycle_days)},
        )

    async def async_step_custom(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a raw service call."""
        errors: dict[str, str] = {}
        if user_input is not None:
            service = str(user_input.get(CONF_RULE_SERVICE, "")).strip()
            if "." not in service or any(char.isspace() for char in service):
                errors["base"] = "invalid_service"
            else:
                draft = {
                    **self._draft,
                    CONF_RULE_SERVICE: service,
                    CONF_RULE_DATA: dict(user_input.get(CONF_RULE_DATA) or {}),
                    CONF_RULE_ENABLED: True,
                }
                # Keep the entity picked on the combined page: a custom service
                # often targets one entity, and it lets the action summary show
                # what the call touches.
                if not draft.get(CONF_RULE_ENTITY):
                    draft.pop(CONF_RULE_ENTITY, None)
                return await self._store_rule(draft)

        defaults = {
            CONF_RULE_SERVICE: self._draft.get(CONF_RULE_SERVICE, ""),
            CONF_RULE_DATA: self._draft.get(CONF_RULE_DATA),
        }
        return self.async_show_form(
            step_id="custom", data_schema=custom_schema(defaults), errors=errors
        )

    async def _store_rule(self, rule: dict[str, Any]) -> ConfigFlowResult:
        """Insert or replace the drafted action."""
        rules = stored_rules(self._working)
        if self._edit_index is not None:
            rules[self._edit_index] = rule
        else:
            if len(rules) >= MAX_RULES:
                return self.async_abort(reason="too_many_rules")
            rules.append(rule)
        self._working = {**self._working, CONF_RULES: rules}
        self._draft = {}
        self._edit_index = None
        return await self.async_step_menu()

    def _store_options(self) -> ConfigFlowResult:
        """Persist the payload; implemented by each flow."""
        raise NotImplementedError


# ------------------------------------------------------------ config flow
class StaggeredSwitchConfigFlow(RuleSteps, ConfigFlow, domain=DOMAIN):
    """Create a new staggered switch cycle."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialise an empty cycle."""
        super().__init__()
        self._working = {CONF_RULES: []}
        self._draft = {}
        self._edit_index = None

    def _store_options(self) -> ConfigFlowResult:
        """Create the entry."""
        return self.async_create_entry(
            title=str(self._working[CONF_NAME]),
            data={},
            options={**self._working, CONF_RULES: stored_rules(self._working)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow for this entry."""
        return StaggeredSwitchOptionsFlow()


# ----------------------------------------------------------- options flow
class StaggeredSwitchOptionsFlow(RuleSteps, OptionsFlow):
    """Edit an existing staggered switch cycle."""

    def __init__(self) -> None:
        """Initialise from the stored payload lazily."""
        super().__init__()
        self._working = {}
        self._draft = {}
        self._edit_index = None
        self._initialised = False

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Seed the payload from the entry the first time."""
        if not self._initialised:
            self._working = dict(
                self.config_entry.options or self.config_entry.data
            )
            self._initialised = True
        return await super().async_step_init(user_input)

    def _store_options(self) -> ConfigFlowResult:
        """Save the options; the update listener reloads the entry."""
        rules = stored_rules(self._working)

        # The visible name lives on the entry title (it names the device and
        # therefore the entity), so keep both in sync when it was edited.
        name = str(self._working.get(CONF_NAME, "")).strip()
        if name and name != self.config_entry.title:
            self.hass.config_entries.async_update_entry(
                self.config_entry, title=name
            )

        return self.async_create_entry(data={**self._working, CONF_RULES: rules})
