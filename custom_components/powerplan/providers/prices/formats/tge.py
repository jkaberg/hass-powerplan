"""TGE: the Polish exchange's fixing sensors (D1 §2).

`PiotrMachowski/Home-Assistant-custom-components-TGE` publishes the Towarowa
Giełda Energii fixings with `prices_today` and `prices_tomorrow` attributes of
`{"time", "price"}` - the same shape as the ENTSO-E row, which is why the reading
is shared and only the units differ.

The units are why this is its own row. TGE works in **złoty per MWh**, and the
sensor's `unit_of_measurement` is `zł/MWh`: a currency symbol, not an ISO code,
so it cannot be trusted as the currency the site's money is in. The currency is
therefore configuration (`PLN` by default) and only the energy scale is taken
from the entity when it happens to name one in ISO form (D-0085). Getting this
wrong is a factor of a thousand, in the direction that makes every hour look
unaffordable.
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


@register
@dataclass(frozen=True, slots=True)
class Tge:
    """Reads a TGE fixing sensor's price lists (D1 §2)."""

    key: ClassVar[str] = "tge"
    platform: ClassVar[str | None] = "tge"
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = (
        Field(key="currency", kind=FieldKind.TEXT, default="PLN", required=True),
        Field(
            key="energy_unit",
            kind=FieldKind.SELECT,
            default=EnergyUnit.MWH.value,
            options=tuple(unit.value for unit in EnergyUnit),
            advanced=True,
        ),
    )

    currency: str = "PLN"
    energy_unit: EnergyUnit = EnergyUnit.MWH

    def parse(self, state: State) -> ParsedPrices:
        """Return the fixing's hourly prices, in the configured currency."""
        declared = declared_unit(state)
        energy = declared[1] if declared else EnergyUnit(self.energy_unit)

        return ParsedPrices(
            intervals=tuple(
                attribute_intervals(
                    state,
                    FORECAST_ATTRIBUTES,
                    start_key="time",
                    value_key="price",
                )
            ),
            currency=self.currency,
            energy=energy,
            magnitude=Magnitude.MAJOR,
        )


__all__ = ["Tge"]
