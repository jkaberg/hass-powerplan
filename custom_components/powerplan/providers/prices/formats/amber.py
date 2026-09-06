"""Amber Electric: the forecast sensor's `forecasts` (D1 §2).

`homeassistant/components/amberelectric` publishes a forecast sensor per channel
whose `forecasts` attribute is a list of intervals - `{duration, date, nem_date,
per_kwh, spot_per_kwh, start_time, end_time, renewables, spike_status,
descriptor}`. `per_kwh` has been through `format_cents_to_dollars()`, so it is
already dollars per kWh: major units, nothing to scale.

The quirk that has to be handled here is the National Electricity Market's
timestamps. The NEM labels an interval by its *end* and opens it one second late -
`04:00:01` to `04:30:00` - so taken literally every Amber slot would be 29
minutes 59 seconds long and every pair of them would have a one-second hole
between them. The seconds are snapped off the start, which restores the slot
length the market actually trades (INV-7); the end is already exact.

Amber's general channel goes negative when the grid is long, and controlled-load
and feed-in channels are negative by nature, so nothing here clamps (INV-51).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.pricing import Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude

from .base import FormatKind, ParsedPrices, attribute_intervals
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State

#: The attribute the forecast sensor carries its intervals on.
FORECASTS_ATTRIBUTE: Final = "forecasts"

#: Amber is an Australian retailer and quotes in dollars per kWh.
CURRENCY: Final = "AUD"


@register
@dataclass(frozen=True, slots=True)
class Amber:
    """Reads an Amber Electric forecast sensor (D1 §2)."""

    key: ClassVar[str] = "amber"
    platform: ClassVar[str | None] = "amber_electric"
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = ()
    # `per_kwh` is the retail price: network charges and GST included (D13 §8, O5).
    basis: ClassVar[frozenset[str]] = frozenset({"spot", "grid", "vat", "levies"})

    def parse(self, state: State) -> ParsedPrices:
        """Return the forecast intervals, with the NEM's second snapped off."""
        intervals = attribute_intervals(
            state,
            (FORECASTS_ATTRIBUTE,),
            start_key="start_time",
            end_key="end_time",
            value_key="per_kwh",
        )

        return ParsedPrices(
            intervals=tuple(
                replace(interval, start=interval.start.replace(second=0, microsecond=0))
                for interval in intervals
            ),
            currency=CURRENCY,
            energy=EnergyUnit.KWH,
            magnitude=Magnitude.MAJOR,
        )


__all__ = ["Amber"]
