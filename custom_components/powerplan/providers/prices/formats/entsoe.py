"""ENTSO-E: `prices_today` / `prices_tomorrow`, else `prices` (D1 §2).

`JaccoR/hass-entso-e` is the integration a household without a retailer
integration uses, anywhere on the European day-ahead market. Its average-price
sensor carries three attributes: `prices_today`, `prices_tomorrow` and `prices`,
the last being today and tomorrow together (or yesterday and today when fewer
than 48 hours are available). Each entry is `{"time": str(datetime), "price":
float}` - `str()` of a local, aware datetime, so the separator is a space and not
a `T`, which `dt_util.parse_datetime` accepts.

There is no end time, so a slot ends where the next begins (INV-7), and no
currency attribute: the entity's `unit_of_measurement` is
`"{currency}/{energy_scale}"` and *that* is where both halves of the unit come
from, read on every parse because the user can change the scale in the other
integration at any time (`design/DECISIONS.md` D-0085). The configured values are
the fallback for a sensor whose unit has been templated away.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.pricing import Field, FieldKind, Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude

from .base import FormatKind, ParsedPrices, attribute_intervals, declared_unit
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State

#: Today and tomorrow, in the order they are read.
FORECAST_ATTRIBUTES: Final = ("prices_today", "prices_tomorrow")

#: What the sensor publishes when it publishes one series instead of two.
COMBINED_ATTRIBUTE: Final = "prices"


@register
@dataclass(frozen=True, slots=True)
class Entsoe:
    """Reads the ENTSO-E sensor's price lists (D1 §2)."""

    key: ClassVar[str] = "entsoe"
    platform: ClassVar[str | None] = "entsoe"
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = (
        Field(key="currency", kind=FieldKind.TEXT, default="EUR", required=True),
        Field(
            key="energy_unit",
            kind=FieldKind.SELECT,
            default=EnergyUnit.KWH.value,
            options=tuple(unit.value for unit in EnergyUnit),
            advanced=True,
        ),
    )

    currency: str = "EUR"
    energy_unit: EnergyUnit = EnergyUnit.KWH

    def parse(self, state: State) -> ParsedPrices:
        """Return today's and tomorrow's prices, or the combined series."""
        declared = declared_unit(state)
        currency, energy = declared if declared else (self.currency, EnergyUnit(self.energy_unit))

        intervals = attribute_intervals(
            state,
            FORECAST_ATTRIBUTES,
            start_key="time",
            value_key="price",
            required=False,
        )
        if not intervals:
            intervals = attribute_intervals(
                state,
                (COMBINED_ATTRIBUTE,),
                start_key="time",
                value_key="price",
            )

        return ParsedPrices(
            intervals=tuple(intervals),
            currency=currency,
            energy=energy,
            magnitude=Magnitude.MAJOR,
        )


__all__ = ["Entsoe"]
