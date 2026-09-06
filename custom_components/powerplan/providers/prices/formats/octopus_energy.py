"""Octopus Energy: the day-rate event entity's `rates` (D1 §2).

`BottlecapDave/HomeAssistant-OctopusEnergy` publishes each day's half-hourly
tariff as an **event** entity - `event.octopus_energy_electricity_<serial>_<mpan>_
current_day_rates` and its `previous_day` and `next_day` siblings - whose `rates`
attribute is a list of `{start, end, value_inc_vat, is_capped,
is_intelligent_adjusted}`. `value_inc_vat` is in pounds, not pence (the docs are
explicit: "1.01 = £1.01"), so nothing here scales it; the integration has already
done the division that the Octopus API's pence would need.

One entity is one day. A site that wants tomorrow as well binds the `next_day`
entity as a second source - the raw store merges them on UTC start, so two
sources of the same market cost nothing (D1 §2).

Agile goes negative on a windy afternoon and that is the whole point of planning
around it, so nothing here clamps (INV-51).
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

#: The attribute the event entity carries its day of rates on.
RATES_ATTRIBUTE: Final = "rates"

#: Octopus is a British supplier and quotes in pounds per kWh, VAT included.
CURRENCY: Final = "GBP"


@register
@dataclass(frozen=True, slots=True)
class OctopusEnergy:
    """Reads an Octopus Energy day-rates event entity (D1 §2)."""

    key: ClassVar[str] = "octopus_energy"
    platform: ClassVar[str | None] = "octopus_energy"
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = ()
    # `value_inc_vat`: a UK supplier unit rate holds DUoS, levies and VAT (D13 §5.10 UK, O5).
    basis: ClassVar[frozenset[str]] = frozenset({"spot", "grid", "vat", "levies"})

    def parse(self, state: State) -> ParsedPrices:
        """Return the entity's half-hourly rates, VAT included."""
        return ParsedPrices(
            intervals=tuple(
                attribute_intervals(
                    state,
                    (RATES_ATTRIBUTE,),
                    start_key="start",
                    end_key="end",
                    value_key="value_inc_vat",
                )
            ),
            currency=CURRENCY,
            energy=EnergyUnit.KWH,
            magnitude=Magnitude.MAJOR,
        )


__all__ = ["OctopusEnergy"]
