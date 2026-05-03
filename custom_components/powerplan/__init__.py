"""The powerplan integration: one config entry per site (HLD §4, §5; D7 §5.5).

`entry.runtime_data` is the site's `Runtime` (D7 §4.3): the engine, its store,
its coordinator, its write gate and every subscription. Services are registered
once in `async_setup` and never per entry (PLAN §7 dec. 8); they arrive with D8
§5.7 in WP1.4.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .runtime import Runtime, build_site

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

# A site is only ever set up from a config entry; powerplan has no YAML surface.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type PowerplanConfigEntry = ConfigEntry[Runtime]

__all__ = [
    "PowerplanConfigEntry",
    "Runtime",
    "async_setup",
    "async_setup_entry",
    "async_unload_entry",
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration once, before any entry.

    Site-wide services (`powerplan.replan`, `powerplan.release`, …) are
    registered here and never per entry (HA rule `action-setup`,
    PLAN §7 dec. 8). None exist yet; they arrive with D8 §5.7 in WP1.4.
    """
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PowerplanConfigEntry) -> bool:
    """Set up one site: D7 §5.5's order, from the store to the triggers (INV-48)."""
    runtime = Runtime(hass, entry, build_site(hass, entry))
    entry.runtime_data = runtime
    await runtime.start()
    _LOGGER.debug("Site %s set up (entry %s): %s", entry.title, entry.entry_id, runtime.startup)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PowerplanConfigEntry) -> bool:
    """Unload one site: stop, release every load (INV-26), flush the store."""
    await entry.runtime_data.stop("unload")
    _LOGGER.debug("Site %s unloaded (entry %s)", entry.title, entry.entry_id)
    return True
