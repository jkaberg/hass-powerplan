"""PVPC: Spain's regulated tariff, hour by hour (D1 §2).

`homeassistant/components/pvpc_hourly_pricing` publishes the day as one attribute
per hour - `price_00h` … `price_23h` - plus `price_next_day_00h` … in the evening
once ESIOS has published tomorrow, and `price_02h_d` for the repeated hour of the
25-hour day. The unit is EUR/kWh throughout.

That is exactly the shape the `hourly_attributes` row describes, with the
prefixes fixed, so the reading is shared: this row is the one the config flow can
pre-select from the platform, and the generic one is what a user configures by
hand for a sensor powerplan has never heard of (D1 §6). The DST fold and the
"today comes from the clock" rule are `hourly_attributes`'s and documented there.

A Spanish site's tariff periods (P1/P2/P3) are D2's business, not this module's:
`tariff` and `period` are on the entity and deliberately ignored here - a price
adapter returns prices, and a price is not a tariff period.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar, Final

from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.pricing import Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices.base import SourceParseError

from .base import FormatKind, ParsedPrices
from .hourly_attributes import hour_intervals
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State

#: The integration's own attribute prefixes (`sensor.py`'s attribute map).
TODAY_PREFIX: Final = "price_"
TOMORROW_PREFIX: Final = "price_next_day_"

#: ESIOS publishes the regulated tariff in euro per kWh.
CURRENCY: Final = "EUR"


@register
@dataclass(frozen=True, slots=True)
class Pvpc:
    """Reads the PVPC sensor's hourly attributes (D1 §2)."""

    key: ClassVar[str] = "pvpc"
    platform: ClassVar[str | None] = "pvpc_hourly_pricing"
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = ()

    def parse(self, state: State) -> ParsedPrices:
        """Return today's hours and, when they are there, tomorrow's."""
        today = dt_util.now().date()
        intervals = hour_intervals(state, prefix=TODAY_PREFIX, day=today)
        if not intervals:
            raise SourceParseError(
                f"{state.entity_id} has no {TODAY_PREFIX}XXh attribute; "
                "this does not look like a PVPC sensor"
            )
        intervals += hour_intervals(state, prefix=TOMORROW_PREFIX, day=today + timedelta(days=1))

        return ParsedPrices(
            intervals=tuple(intervals),
            currency=CURRENCY,
            energy=EnergyUnit.KWH,
            magnitude=Magnitude.MAJOR,
        )


__all__ = ["Pvpc"]
