"""The site's number (D8 §5.5): `number.<site>_margin_kwh`, the guard band ε (D2 §6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfEnergy

from .core.tariffs.target import EPS_DEFAULT_KWH_PER_HOUR, EPS_MAX_KWH
from .entity import PowerplanEntity
from .load_entities import load_numbers

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import PowerplanConfigEntry
    from .runtime import Runtime

EPS_MIN_KWH = 0.05
EPS_STEP_KWH = 0.05


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerplanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the margin number."""
    async_add_entities([MarginNumber(entry.runtime_data), *load_numbers(entry.runtime_data)])


class MarginNumber(PowerplanEntity, RestoreNumber, NumberEntity):
    """ε in kWh per window hour; absolute energy, never a percentage (D2 §6)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_native_min_value = EPS_MIN_KWH
    _attr_native_max_value = EPS_MAX_KWH
    _attr_native_step = EPS_STEP_KWH
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:arrow-collapse-down"

    def __init__(self, runtime: Runtime) -> None:
        """Bind to the site."""
        super().__init__(runtime, "margin_kwh")

    async def async_added_to_hass(self) -> None:
        """Restore the last value and push it to the runtime (INV-47)."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            value = float(last.native_value)
            if value != self.runtime.eps_kwh:
                await self.runtime.async_set_eps(value)

    @property
    def native_value(self) -> float:
        """The margin in force, the site default when none was set."""
        eps = self.runtime.eps_kwh
        return EPS_DEFAULT_KWH_PER_HOUR if eps is None else eps

    async def async_set_native_value(self, value: float) -> None:
        """Set the margin."""
        await self.runtime.async_set_eps(value)
        self.async_write_ha_state()
