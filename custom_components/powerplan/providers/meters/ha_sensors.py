"""v1's meter source: the site's own Home Assistant sensors (D3 §3, §6).

The seven roles of D3 §6's meter step - grid power, the import register, an
optional export register, production, L1/L2/L3 currents and the meter's own
window value - read through `hass.states.get` once per tick into one
`MeterSample`. Only grid power and the import register matter for the capacity
axis; a site with neither still runs on the price axis, with the capacity axis
explicitly off (INV-53).

Nothing is subscribed here. `entity_ids()` is what the runtime hands
`async_track_state_change_event`, so the subscription's lifetime belongs to the
config entry that owns it (INV-3, D7 §5.3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from custom_components.powerplan.core.metering import MeterSample, Quality, Reading

from .base import EntityReader

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HaSensorsConfig:
    """The entity each role is bound to (D3 §6, meter step).

    Every role is optional, including grid power: the *price only* path skips
    the meter step altogether and the capacity axis switches off (INV-53).
    `phase_current` is positional - index 0 is L1 - so an unbound middle phase
    keeps the phases that follow it on the right index.
    """

    grid_power: str | None = None
    import_register: str | None = None
    export_register: str | None = None
    production_power: str | None = None
    meter_window: str | None = None
    phase_current: tuple[str | None, str | None, str | None] = (None, None, None)


class HaSensorsMeter:
    """Reads the site's meter from ordinary HA sensor entities (D3 §3)."""

    key: ClassVar[str] = "ha_sensors"

    def __init__(self, hass: HomeAssistant, config: HaSensorsConfig) -> None:
        """Bind the source to `hass` and to the entities `config` names."""
        self._config = config
        self._reader = EntityReader(hass)

    async def sample(self, now: datetime) -> MeterSample:
        """Read every bound entity once, at `now` (D3 §4)."""
        cfg = self._config
        reader = self._reader
        sample = MeterSample(
            grid_w=reader.power_w(cfg.grid_power, now),
            import_kwh=reader.energy_kwh(cfg.import_register, now),
            export_kwh=reader.energy_kwh(cfg.export_register, now),
            production_w=reader.power_w(cfg.production_power, now),
            meter_window_kwh=reader.energy_kwh(cfg.meter_window, now),
            meter_window_start=reader.last_reset(cfg.meter_window),
            phase_a=self._phase_currents(now),
        )
        _LOGGER.debug(
            "meter sample: grid=%s import=%s phases=%s",
            sample.grid_w,
            sample.import_kwh,
            sample.phase_a,
        )
        return sample

    def entity_ids(self) -> frozenset[str]:
        """Return every entity bound to a role (D7 §5.3 subscribes to these)."""
        cfg = self._config
        bound = (
            cfg.grid_power,
            cfg.import_register,
            cfg.export_register,
            cfg.production_power,
            cfg.meter_window,
            *cfg.phase_current,
        )
        return frozenset(entity_id for entity_id in bound if entity_id)

    def _phase_currents(self, now: datetime) -> tuple[Reading, ...] | None:
        """Read the bound phase currents, keeping L1/L2/L3 on their own index.

        `None` when no phase is bound at all. Otherwise the tuple runs from L1 to
        the last bound phase, and an unbound hole in between is `UNAVAILABLE`
        rather than absent - `PhaseReadings` indexes by position, and shifting L3
        into L2's slot would put a load's headroom on the wrong phase. A hole
        makes `WindowMeter` drop the whole set, which is the conservative answer.
        """
        bound = self._config.phase_current
        last = max((index for index, entity_id in enumerate(bound) if entity_id), default=-1)
        if last < 0:
            return None
        readings: list[Reading] = []
        for index, entity_id in enumerate(bound[: last + 1]):
            reading = self._reader.current_a(entity_id, now)
            if reading is None:
                reading = Reading(
                    value=0.0,
                    at=now,
                    source=f"L{index + 1}",
                    quality=Quality.UNAVAILABLE,
                )
            readings.append(reading)
        return tuple(readings)


def value_now(hass: HomeAssistant, entity_id: str, quantity: str, now: datetime) -> float | None:
    """Return one sensor's value now - W, kWh or A by `quantity` - or `None` if it has none.

    The site flow shows each meter role it found with its current value, so a
    household can tell the export register from the import one by what they
    read (D3 §6, D8 §9 21 (d)); `hass.states` stays behind a provider (INV-3).
    """
    reader = EntityReader(hass)
    read = {"power": reader.power_w, "energy": reader.energy_kwh, "current": reader.current_a}
    reading = read[quantity](entity_id, now)
    if reading is None or reading.quality is not Quality.OK:
        return None
    return reading.value


__all__ = ["HaSensorsConfig", "HaSensorsMeter", "value_now"]
