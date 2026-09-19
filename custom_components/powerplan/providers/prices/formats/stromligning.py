"""Strømligning: the price sensor's `prices` (D1 §2).

`MTrab/stromligning` (`sensor.py`, `build_price_attributes`) writes each price
sensor's series as `prices: [{price, start, end}]` in `kr/kWh` - Danish kroner,
though the unit says only "kr". The *current price* sensor's `price` is
`price.total`: spot, the grid company's tariff, Energinet's tariffs, the
electricity tax and VAT, all in. Its basis says so, so the chain adds neither the
grid's energy charge nor the state stage on top (D13 §8, O5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.pricing import Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude

from .base import FormatKind, ParsedPrices, attribute_intervals
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State

#: The one attribute every slot is on.
PRICES_ATTRIBUTE: Final = "prices"


@register
@dataclass(frozen=True, slots=True)
class Stromligning:
    """Reads a Strømligning price sensor (D1 §2)."""

    key: ClassVar[str] = "stromligning"
    platform: ClassVar[str | None] = "stromligning"
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = ()
    basis: ClassVar[frozenset[str]] = frozenset({"spot", "grid", "vat", "levies"})

    def parse(self, state: State) -> ParsedPrices:
        """Return the sensor's slots, in Danish kroner per kWh."""
        return ParsedPrices(
            intervals=tuple(
                attribute_intervals(
                    state,
                    (PRICES_ATTRIBUTE,),
                    start_key="start",
                    end_key="end",
                    value_key="price",
                )
            ),
            currency="DKK",
            energy=EnergyUnit.KWH,
            magnitude=Magnitude.MAJOR,
        )


__all__ = ["Stromligning"]
