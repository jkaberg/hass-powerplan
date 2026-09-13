"""`powerplan.get_dashboard` (D12 §5.16 R1): the strategy's one call, the layout back.

A response action (`SupportsResponse.ONLY`, `services.py`), so any signed-in
user may call it (D12 §9 4). An unknown or unloaded site is `unknown_site`,
never an empty dashboard; with no `site` every loaded site is meant, so a
second site appears on the dashboard the next time it opens (D-0439).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import __version__ as HA_VERSION  # noqa: N812 - HA's own name
from homeassistant.helpers.translation import async_get_translations

from custom_components.powerplan.const import DOMAIN

from .layout import ENTITY_NAMES, build
from .site_layout import site_layout

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.runtime import Runtime

__all__ = ["async_dashboard_config", "texts_from"]

_COMPONENT = f"component.{DOMAIN}."


def texts_from(strings: Mapping[str, str]) -> dict[str, str]:
    """Return `build`'s texts from the integration's flattened translations.

    The dashboard's own words (`selector.dashboard.options`), each named
    entity's name as `entity_<key>` (`ENTITY_NAMES`), each strategy's words
    as `strategy_<key>` (`selector.strategy.options`, the flows' own labels) and
    each device type's name as `type_<key>` (`selector.load_type.options`).
    """
    options = f"{_COMPONENT}selector.dashboard.options."
    strategies = f"{_COMPONENT}selector.strategy.options."
    types = f"{_COMPONENT}selector.load_type.options."
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
    texts.update(
        {
            f"type_{key.removeprefix(types)}": value
            for key, value in strings.items()
            if key.startswith(types)
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


async def async_dashboard_config(
    hass: HomeAssistant,
    runtimes: Sequence[Runtime],
    *,
    language: str,
    hidden_views: Sequence[str],
    hidden_cards: Sequence[str],
) -> dict[str, Any]:
    """Return the dashboard config of the loaded sites the action named."""
    return build(
        [site_layout(hass, runtime.entry) for runtime in runtimes],
        HA_VERSION,
        await _texts(hass, language),
        language=language,
        grid_statistics=await _grid_statistics(hass),
        hidden_views=list(hidden_views),
        hidden_cards=list(hidden_cards),
    )
