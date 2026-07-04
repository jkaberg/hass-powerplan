"""The site's buttons (D8 §5.5): `button.<site>_replan`, `button.<site>_rebuild_baseline`.

`rebuild_baseline` arrives only where a meter is bound - a button
without an action behind it would be a lie on the device page.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import PowerplanEntity
from .load_entities import load_buttons

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import PowerplanConfigEntry
    from .runtime import Runtime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerplanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the replan button, and `rebuild_baseline` where a meter is bound."""
    runtime = entry.runtime_data
    entities: list[ButtonEntity] = [ReplanButton(runtime)]
    if runtime.forecasts_adapter is not None:
        entities.append(RebuildBaselineButton(runtime))
    async_add_entities(entities)
    runtime.setup_load_platform(async_add_entities, load_buttons)


class ReplanButton(PowerplanEntity, ButtonEntity):
    """`button.<site>_replan`: a planning cycle now (D8 §5.7 `powerplan.replan`)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:calendar-refresh"

    def __init__(self, runtime: Runtime) -> None:
        """Bind to the site."""
        super().__init__(runtime, "replan")

    async def async_press(self) -> None:
        """Fetch what is missing and plan."""
        await self.runtime.async_replan()


class RebuildBaselineButton(PowerplanEntity, ButtonEntity):
    """`button.<site>_rebuild_baseline`: re-seed D10's baseline from the recorder (D10 §6)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:chart-bell-curve"
    _attr_entity_registry_enabled_default = False

    def __init__(self, runtime: Runtime) -> None:
        """Bind to the site."""
        super().__init__(runtime, "rebuild_baseline")

    async def async_press(self) -> None:
        """Discard the learned profile and seed a fresh one from the recorder."""
        await self.runtime.async_rebuild_baseline()
