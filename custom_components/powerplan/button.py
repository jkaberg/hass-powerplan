"""The site's buttons (D8 §5.5): `button.<site>_replan`.

`rebuild_peak_history` and `rebuild_baseline` arrive with the seeds they press
(D2's recorder seed in WP1.6, D10's in WP5.1); a button without an action
behind it would be a lie on the device page.
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
    """Create the replan button."""
    runtime = entry.runtime_data
    async_add_entities([ReplanButton(runtime)])
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
