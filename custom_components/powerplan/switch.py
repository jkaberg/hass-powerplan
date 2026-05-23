"""The site switch (D8 §5.5): `switch.<site>_active`, the master.

Off releases every load on the edge (INV-26) and the site observes: decisions
are still published (INV-44) and D11's calibration slots accrue. The last
state is restored on start and pushed to the runtime, so a site switched off
stays off across a restart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import PowerplanEntity
from .load_entities import load_switches

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import PowerplanConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerplanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the master switch."""
    async_add_entities([ActiveSwitch(entry.runtime_data), *load_switches(entry.runtime_data)])


class ActiveSwitch(PowerplanEntity, SwitchEntity, RestoreEntity):
    """`switch.<site>_active`."""

    _attr_icon = "mdi:power"

    def __init__(self, runtime: Any) -> None:
        """Bind to the site."""
        super().__init__(runtime, "active")

    async def async_added_to_hass(self) -> None:
        """Restore the last position and push it to the runtime (INV-47)."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in (STATE_ON, STATE_OFF):
            wanted = last.state == STATE_ON
            if wanted != self.runtime.active:
                await self.runtime.async_set_active(wanted)

    @property
    def is_on(self) -> bool:
        """Whether the site steers."""
        return self.runtime.active

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Steer again."""
        await self.runtime.async_set_active(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Release every load and observe."""
        await self.runtime.async_set_active(False)
        self.async_write_ha_state()
