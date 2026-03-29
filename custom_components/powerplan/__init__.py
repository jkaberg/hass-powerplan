"""The powerplan integration - loadable shell.

HLD §4 and §5: one config entry per site, `entry.runtime_data` typed as
`PowerplanConfigEntry`, services registered once in `async_setup`
(PLAN §7 dec. 8). The real lifecycle - restore stores, release every load,
restore setpoints, provision profiles, first tick, forward platforms
(INV-48) - is built in WP1.1; this module exists so the integration is
loadable in Home Assistant from day one (PLAN §8, "vertical slice first").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

# A site is only ever set up from a config entry; powerplan has no YAML surface.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class Runtime:
    """Per-site runtime state held on the config entry (D7 §4.3).

    A placeholder: the coordinator, the engine lock, the subscriptions and the
    stores are added in WP1.1. It is deliberately not frozen - the real
    `Runtime` is mutable, unlike everything that crosses a layer in `core/`.
    """

    site_name: str


type PowerplanConfigEntry = ConfigEntry[Runtime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration once, before any entry.

    Site-wide services (`powerplan.replan`, `powerplan.release`, …) are
    registered here and never per entry (HA rule `action-setup`,
    PLAN §7 dec. 8). None exist yet; they arrive with D8 §5.7 in WP1.4.
    """
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PowerplanConfigEntry) -> bool:
    """Set up one site."""
    entry.runtime_data = Runtime(site_name=entry.title)
    _LOGGER.debug("Site %s set up (entry %s)", entry.title, entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PowerplanConfigEntry) -> bool:
    """Unload one site.

    From WP1.1 on this stops the engine, releases every load (INV-26) and
    flushes the store. There is nothing to release yet.
    """
    _LOGGER.debug("Site %s unloaded (entry %s)", entry.title, entry.entry_id)
    return True
