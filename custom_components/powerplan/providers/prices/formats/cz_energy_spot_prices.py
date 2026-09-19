"""Czech Energy Spot Prices: one attribute per slot, keyed by its start (D1 §2).

`rnovacek/homeassistant_cz_energy_spot_prices` (`sensor.py`,
`SpotRateElectricitySensor.update`) writes today's and tomorrow's slots as
attributes of the price sensor itself - the key is the slot's local start in ISO
form, the value its price - and the unit as `Kč/kWh`, `€/kWh`, `Kč/MWh` or
`€/MWh` (`currency_human` in `__init__.py`). An attribute whose key is not a
timestamp (`friendly_name`, `unit_of_measurement`) is not a slot.

Only starts are published, so a slot ends where the next begins (INV-7). The
spot sensor is the market price alone; the integration's buy and sell sensors
apply the household's own template, and are not what this row reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.pricing import Schema
from custom_components.powerplan.core.pricing.normalise import Magnitude
from custom_components.powerplan.providers.prices.base import Interval, SourceParseError

from .base import FormatKind, ParsedPrices, price, symbol_unit
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State


@register
@dataclass(frozen=True, slots=True)
class CzEnergySpotPrices:
    """Reads a Czech Energy Spot Prices sensor (D1 §2)."""

    key: ClassVar[str] = "cz_energy_spot_prices"
    platform: ClassVar[str | None] = "cz_energy_spot_prices"
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = ()

    def parse(self, state: State) -> ParsedPrices:
        """Return every timestamp-keyed attribute as a slot start and its price."""
        unit = symbol_unit(state)
        if unit is None:
            raise SourceParseError(
                f"{state.entity_id}'s unit {state.attributes.get('unit_of_measurement')!r} "
                "names no currency"
            )
        intervals: list[Interval] = []
        for key, value in state.attributes.items():
            start = dt_util.parse_datetime(key) if isinstance(key, str) else None
            if start is None or value is None:
                continue
            intervals.append(
                Interval(
                    start=start, end=None, value=price(value, where=f"{state.entity_id}.{key}")
                )
            )
        if not intervals:
            raise SourceParseError(f"{state.entity_id} has no timestamped price attribute")
        return ParsedPrices(
            intervals=tuple(sorted(intervals, key=lambda row: row.start)),
            currency=unit[0],
            energy=unit[1],
            magnitude=Magnitude.MAJOR,
        )


__all__ = ["CzEnergySpotPrices"]
