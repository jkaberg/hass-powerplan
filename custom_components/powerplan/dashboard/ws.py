"""`powerplan/dashboard/config` (D12 §3): the strategy's one call, the layout back.

Read-only, so any signed-in user may call it (D12 §9 4). An unknown or
unloaded site is an error, never an empty dashboard; with no `entry_id` every loaded
site is meant, so a second site appears on the dashboard the next time it
opens (D-0439).
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

from .layout import ENTITY_NAMES, build
from .site_layout import site_layout

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.components.websocket_api.connection import ActiveConnection
    from homeassistant.core import HomeAssistant

__all__ = ["WS_TYPE", "async_register_ws"]

WS_TYPE = "powerplan/dashboard/config"
_COMPONENT = f"component.{DOMAIN}."


@callback
def async_register_ws(hass: HomeAssistant) -> None:
    """Register the command once per HA."""
    async_register_command(hass, ws_dashboard_config)


def texts_from(strings: Mapping[str, str]) -> dict[str, str]:
    """Return `build`'s texts from the integration's flattened translations.

    The dashboard's own words (`selector.dashboard.options`), each named
    entity's name as `entity_<key>` (`ENTITY_NAMES`) and each strategy's words
    as `strategy_<key>` (`selector.strategy.options`, the flows' own labels).
    """
    options = f"{_COMPONENT}selector.dashboard.options."
    strategies = f"{_COMPONENT}selector.strategy.options."
    texts = {
        key.removeprefix(options): value
        for key, value in strings.items()
        if key.startswith(options)
    }
    texts.update(
        {
            f"strategy_{key.removeprefix(strategies)}": value
            for key, value in strings.items()
            if key.startswith(strategies)
        }
    )
    for key, (platform, name) in ENTITY_NAMES.items():
        texts[f"entity_{key}"] = strings[f"{_COMPONENT}entity.{platform}.{name}.name"]
    return texts


async def _texts(hass: HomeAssistant, language: str) -> dict[str, str]:
    """Return the dashboard's words in `language`; HA falls back to English per key."""
    strings = {
        **await async_get_translations(hass, language, "selector", {DOMAIN}),
        **await async_get_translations(hass, language, "entity", {DOMAIN}),
    }
    return texts_from(strings)


async def _grid_statistics(hass: HomeAssistant) -> list[str]:
    """Return the Energy preferences' grid consumption statistics (D12 §5.7).

    Both shapes HA keeps: a grid source's own `stat_energy_from` (2026.x) and
    the older `flow_from` list it migrates from.
    """
    if "energy" not in hass.config.components:
        return []
    from homeassistant.components.energy.data import async_get_manager  # noqa: PLC0415

    prefs = (await async_get_manager(hass)).data
    out: list[str] = []
    for source in [] if prefs is None else prefs.get("energy_sources", []):
        if source["type"] != "grid":
            continue
        legacy: list[Any] = list(source.get("flow_from") or [])  # type: ignore[call-overload]
        rows: list[Any] = [source, *legacy]
        out += [row["stat_energy_from"] for row in rows if row.get("stat_energy_from")]
    return out


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
        language=msg["language"],
        grid_statistics=await _grid_statistics(hass),
        hidden_views=msg["hidden_views"],
        hidden_cards=msg["hidden_cards"],
    )
    connection.send_result(msg["id"], config)
