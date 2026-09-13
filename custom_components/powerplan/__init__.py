"""The powerplan integration: one config entry per site (HLD §4, §5; D7 §5.5).

`entry.runtime_data` is the site's `Runtime` (D7 §4.3): the engine, its store,
its coordinator, its write gate and every subscription. Services are registered
once in `async_setup` and never per entry (PLAN §7 dec. 8); they arrive with D8
§5.7 in WP1.4.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components.http.server import StaticPathConfig
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    BRAND_ICON_URL,
    DOMAIN,
    LOAD_BINDINGS,
    LOAD_PARAMS,
    LOAD_PROFILE,
    LOAD_TYPE,
    SUBENTRY_LOAD,
)
from .dashboard import async_remove_dashboard, async_setup_dashboard
from .entity import async_prepare_site_device
from .flow.load import binding_from_data, binding_to_data, extra_bindings
from .runtime import Runtime, build_site
from .services import async_setup_services
from .storage import ENTRY_MINOR_PRICE, migrate_tariff

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
    "async_remove_entry",
    "async_setup",
    "async_setup_entry",
    "async_unload_entry",
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration once, before any entry.

    Site-wide services (`powerplan.replan`, `powerplan.release`, …) are
    registered here and never per entry (HA rule `action-setup`,
    PLAN §7 dec. 8; D8 §5.7).
    """
    async_setup_services(hass)
    # The brand icon an appliance entity shows as its picture (D8 §5.16,
    # D-0418). `http` is an after-dependency: every frontend loads it, a bare
    # test harness may not.
    if hass.http is not None:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(BRAND_ICON_URL, str(Path(__file__).parent / "brand" / "icon.png"))]
        )
    await async_setup_dashboard(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: PowerplanConfigEntry) -> bool:
    """Bring an older entry to the current schema, offline (D13 §10, INV-73).

    Minor version 2: the tariff becomes the copy by party (`tariff.price`), the
    preset's energy charge leaves the add-ons for it, and an old VAT or levy
    add-on becomes the state stage - dropped where the country module says the
    same, kept (and raised as `tariff_review`) where it differs. Nothing is
    fetched: the files it reads ship with the release.
    """
    if entry.version > 1:
        return False
    if entry.minor_version < ENTRY_MINOR_PRICE:
        data, review = await hass.async_add_executor_job(
            migrate_tariff, dict(entry.data), dt_util.now().date()
        )
        hass.config_entries.async_update_entry(entry, data=data, minor_version=ENTRY_MINOR_PRICE)
        _LOGGER.info(
            "%s: tariff migrated to the copy by party%s",
            entry.title,
            f"; kept for review: {', '.join(review)}" if review else "",
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PowerplanConfigEntry) -> bool:
    """Set up one site: D7 §5.5's order, from the store to the triggers (INV-48)."""
    # build_site() reads two preset JSON files and, for a non-default country,
    # imports and runs the holidays library's own table builder - all blocking,
    # so it runs off the event loop (HA's rule: no blocking
    # call in the loop). Nothing inside it needs the loop: every state and
    # config read it makes is a fast, in-memory one HA allows from either
    # thread.
    _bind_answered_roles(hass, entry)
    site = await hass.async_add_executor_job(build_site, hass, entry)
    runtime = Runtime(hass, entry, site)
    entry.runtime_data = runtime
    await async_prepare_site_device(hass, runtime)
    await runtime.start()
    # A load or circuit subentry added, changed or removed patches the site in
    # place (D7 §2); the site's own `entry.data` still reloads it, which
    # releases every load on the way out (INV-26) and restores on the way back
    # in (INV-48) - `Runtime.async_handle_subentry_update` tells them apart.
    entry.async_on_unload(entry.add_update_listener(_async_handle_update))
    _LOGGER.debug("Site %s set up (entry %s): %s", entry.title, entry.entry_id, runtime.startup)
    return True


def _bind_answered_roles(hass: HomeAssistant, entry: PowerplanConfigEntry) -> None:
    """Bind an answered off-device sensor a subentry saved before the flow bound it (D-0485).

    Before `e9ba672` the car's `soc_entity` answer stayed a parameter: `Role.SOC`
    read `None` and the EV was never planned. Only a role the subentry lacks is
    added, so a match-step binding stands; an entity with no state yet binds on
    a later start. Runs before the update listener exists, so it reloads nothing.
    """
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_LOAD:
            continue
        data = subentry.data
        rows = list(data.get(LOAD_BINDINGS) or ())
        bound = {binding_from_data(row).role for row in rows}
        missing = [
            binding
            for binding in extra_bindings(
                hass,
                str(data.get(LOAD_TYPE)),
                data.get(LOAD_PARAMS) or {},
                profile=str(data.get(LOAD_PROFILE) or ""),
            )
            if binding.role not in bound
        ]
        if not missing:
            continue
        _LOGGER.info(
            "%s: binding %s from its answers",
            subentry.title,
            ", ".join(binding.role.value for binding in missing),
        )
        hass.config_entries.async_update_subentry(
            entry,
            subentry,
            data={**data, LOAD_BINDINGS: [*rows, *(binding_to_data(b) for b in missing)]},
        )


async def _async_handle_update(hass: HomeAssistant, entry: PowerplanConfigEntry) -> None:
    """Apply a subentry hot path, or reload the site when its own data changed (D7 §2)."""
    del hass
    await entry.runtime_data.async_handle_subentry_update()


async def async_unload_entry(hass: HomeAssistant, entry: PowerplanConfigEntry) -> bool:
    """Unload one site: stop, release every load (INV-26), flush the store."""
    await entry.runtime_data.stop("unload")
    _LOGGER.debug("Site %s unloaded (entry %s)", entry.title, entry.entry_id)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: PowerplanConfigEntry) -> None:
    """Remove one site; with the last one, the dashboard's Lovelace resource too (D12 §5.16 R4)."""
    if not any(
        other.entry_id != entry.entry_id for other in hass.config_entries.async_entries(DOMAIN)
    ):
        await async_remove_dashboard(hass)
