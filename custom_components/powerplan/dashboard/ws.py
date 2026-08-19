"""`powerplan/dashboard/config` (D12 §3): the strategy's one call, the layout back.

Read-only, so any signed-in user may call it (D12 §9 4). An unknown or
unloaded site is an error, never an empty dashboard; with no `entry_id` the one
loaded site is meant - every one of them, four views each, so a second site
appears on the dashboard the next time it opens.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.websocket_api import async_register_command
from homeassistant.components.websocket_api.const import ERR_NOT_FOUND
from homeassistant.components.websocket_api.decorators import async_response, websocket_command
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import __version__ as HA_VERSION  # noqa: N812 - HA's own name
from homeassistant.core import callback
from homeassistant.helpers.translation import async_get_translations

from custom_components.powerplan.const import DOMAIN

from .layout import build
from .site_layout import site_layout

if TYPE_CHECKING:
    from homeassistant.components.websocket_api.connection import ActiveConnection
    from homeassistant.core import HomeAssistant

__all__ = ["WS_TYPE", "async_register_ws"]

WS_TYPE = "powerplan/dashboard/config"
#: The headings' place in the integration's translations; HA falls back to English.
_HEADINGS = f"component.{DOMAIN}.selector.dashboard.options."


@callback
def async_register_ws(hass: HomeAssistant) -> None:
    """Register the command once per HA."""
    async_register_command(hass, ws_dashboard_config)


async def _texts(hass: HomeAssistant, language: str) -> dict[str, str]:
    """Return the headings in `language`, from `selector.dashboard` (the `flow/text.py` pattern)."""
    strings = await async_get_translations(hass, language, "selector", {DOMAIN})
    return {
        key.removeprefix(_HEADINGS): value
        for key, value in strings.items()
        if key.startswith(_HEADINGS)
    }


async def _has_energy_grid(hass: HomeAssistant) -> bool:
    """Whether the household's Energy preferences have a grid source (D12 §5.1, `history`)."""
    if "energy" not in hass.config.components:
        return False
    from homeassistant.components.energy.data import async_get_manager  # noqa: PLC0415

    prefs = (await async_get_manager(hass)).data
    return prefs is not None and any(
        source["type"] == "grid" for source in prefs.get("energy_sources", [])
    )


@websocket_command(
    {
        vol.Required("type"): WS_TYPE,
        vol.Optional("entry_id"): str,
        vol.Optional("language", default="en"): str,
        vol.Optional("hidden_views", default=[]): [str],
        vol.Optional("hidden_cards", default=[]): [str],
    }
)
@async_response
async def ws_dashboard_config(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the dashboard config of one loaded site."""
    loaded = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if "entry_id" in msg:
        loaded = [entry for entry in loaded if entry.entry_id == msg["entry_id"]]
    if not loaded:
        connection.send_error(msg["id"], ERR_NOT_FOUND, "No loaded PowerPlan site")
        return
    config = build(
        [site_layout(hass, entry) for entry in loaded],
        HA_VERSION,
        await _texts(hass, msg["language"]),
        has_energy_grid=await _has_energy_grid(hass),
        hidden_views=msg["hidden_views"],
        hidden_cards=msg["hidden_cards"],
    )
    connection.send_result(msg["id"], config)
