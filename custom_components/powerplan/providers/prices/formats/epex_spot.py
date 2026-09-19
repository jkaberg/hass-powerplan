"""EPEX Spot: the market-price sensor's `data` list (D1 §2).

`mampfes/ha_epex_spot` (`custom_components/epex_spot/sensor.py`) writes every
known slot of its market-price sensor to one attribute, `data`, as
`{start_time, end_time, price_per_kwh}` with local ISO timestamps, and its unit as
`€/kWh` or `£/kWh` (`localization.py`) - a symbol, not an ISO code, so the
currency is read through `symbol_unit`. The same `data` attribute is on the
integration's volume and rank sensors without a `price_per_kwh`; every row of
those is then a hole and the source reports that it has no prices.

The row reads the **market price** sensor: the spot price alone (O5). The
integration's *total price* sensor adds the household's own surcharge and tax
settings, and binding it would count them twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.pricing import Schema
from custom_components.powerplan.core.pricing.normalise import Magnitude
from custom_components.powerplan.providers.prices.base import SourceParseError

from .base import FormatKind, ParsedPrices, attribute_intervals, symbol_unit
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State

#: The one attribute every slot is on.
DATA_ATTRIBUTE: Final = "data"


@register
@dataclass(frozen=True, slots=True)
class EpexSpot:
    """Reads an EPEX Spot market-price sensor (D1 §2)."""

    key: ClassVar[str] = "epex_spot"
    platform: ClassVar[str | None] = "epex_spot"
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = ()

    def parse(self, state: State) -> ParsedPrices:
        """Return the sensor's slots, in the currency its unit names."""
        unit = symbol_unit(state)
        if unit is None:
            raise SourceParseError(
                f"{state.entity_id}'s unit {state.attributes.get('unit_of_measurement')!r} "
                "names no currency"
            )
        return ParsedPrices(
            intervals=tuple(
                attribute_intervals(
                    state,
                    (DATA_ATTRIBUTE,),
                    start_key="start_time",
                    end_key="end_time",
                    value_key="price_per_kwh",
                )
            ),
            currency=unit[0],
            energy=unit[1],
            magnitude=Magnitude.MAJOR,
        )


__all__ = ["EpexSpot"]
