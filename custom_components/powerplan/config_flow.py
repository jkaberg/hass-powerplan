"""Site config flow (D8 §5.1).

WP0.1 ships the first step only: a name. The onboarding path menu and the
electrical, meter, prices, tariff, presence, notification and review steps are
WP1.3; they render from the domain registries' schemas, not from conditionals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.core import callback

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, ConfigSubentryFlow

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_NAME, default="Home"): str})


class PowerplanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for one site."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Name the site and create its entry."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

        name: str = user_input[CONF_NAME]
        return self.async_create_entry(title=name, data={CONF_NAME: name})

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types this site supports.

        Loads, groups, zones and circuits are config subentries with only a
        `user` and a `reconfigure` step (D8 §5.2–5.3, PLAN §7 dec. 4). The load
        flow lands in WP2.4, circuits in WP2.5, groups and zones in WP3.2 and
        WP5.3; until then a site has no subentry types.
        """
        return {}
