"""Frank Energie: `prices` and `tomorrow_prices` (D1 §2).

`HiDiHo01/home-assistant-frank_energie` (`sensor.py`) puts the price series on
its sensors through `python_frank_energie`'s `PriceData.asdict`, which returns
`[{from, till, price}]` localised to Amsterdam, in `€/kWh`. The current-price
sensor carries today as `prices`; tomorrow's average sensor carries tomorrow as
`tomorrow_prices`, which is `[{"message": "No prices for tomorrow."}]` before
publication - a row without a price, so nothing, never a zero.

The row reads the all-in current-price sensor (`asdict("total")`): market price,
Frank's sourcing markup, the Dutch energy tax and VAT (O5).
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

#: Today and tomorrow, in the order they are read.
PRICE_ATTRIBUTES: Final = ("prices", "tomorrow_prices")


@register
@dataclass(frozen=True, slots=True)
class FrankEnergie:
    """Reads a Frank Energie price sensor (D1 §2)."""

    key: ClassVar[str] = "frank_energie"
    platform: ClassVar[str | None] = "frank_energie"
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = ()
    basis: ClassVar[frozenset[str]] = frozenset({"spot", "vat", "levies"})

    def parse(self, state: State) -> ParsedPrices:
        """Return the sensor's slots, in euro per kWh."""
        return ParsedPrices(
            intervals=tuple(
                attribute_intervals(
                    state,
                    PRICE_ATTRIBUTES,
                    start_key="from",
                    end_key="till",
                    value_key="price",
                )
            ),
            currency="EUR",
            energy=EnergyUnit.KWH,
            magnitude=Magnitude.MAJOR,
        )


__all__ = ["FrankEnergie"]
