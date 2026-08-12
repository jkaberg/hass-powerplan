"""The site's buttons (D8 §5.5): `replan`, `rebuild_baseline`, `rebuild_peak_history`.

`rebuild_baseline` and `rebuild_peak_history` arrive only where
an import register is bound - a button without an action behind it would be a
lie on the device page.
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

#: Every entity is pushed by the coordinator; none polls (HA rule `parallel-updates`).
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerplanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the replan button, and the two recorder rebuilds where a register is bound."""
    runtime = entry.runtime_data
    entities: list[ButtonEntity] = [ReplanButton(runtime)]
    if runtime.forecasts_adapter is not None:
        entities.append(RebuildBaselineButton(runtime))
    if runtime.has_register:
        entities.append(RebuildPeakHistoryButton(runtime))
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


class RebuildPeakHistoryButton(PowerplanEntity, ButtonEntity):
    """`button.<site>_rebuild_peak_history`: the open period again from the recorder (D2 §5.12)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:chart-timeline-variant-shimmer"
    _attr_entity_registry_enabled_default = False

    def __init__(self, runtime: Runtime) -> None:
        """Bind to the site."""
        super().__init__(runtime, "rebuild_peak_history")

    async def async_press(self) -> None:
        """Replace the period's windows with the import register's; overrides stay."""
        await self.runtime.async_rebuild_peak_history()
