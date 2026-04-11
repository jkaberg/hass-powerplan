"""The HACS Nord Pool sensor: `raw_today` / `raw_tomorrow` (D1 §2, row 1).

`custom_components/nordpool` (custom-components/nordpool) publishes one sensor per
area whose `raw_today` and `raw_tomorrow` attributes are lists of
`{start, end, value}` - built by its own `_add_raw`, with `value` already through
its `_calc_price`, so VAT and the user's additional costs are in the number if
they configured them there. `unit` is its `price_type` (`kWh` / `MWh` / `Wh`) and
`currency` is the area's currency.

Two things the upstream code makes unavoidable: `value` is `None` for an hour it
could not price (`if value is None or math.isinf(value): return None`), and the
lists are the *local* day, so a DST day is genuinely 23 or 25 hours long and the
autumn repeat appears twice with different offsets. Both are handled here, not
downstream (INV-7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.pricing import Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices.base import SourceParseError

from .base import FormatKind, ParsedPrices, attribute_intervals
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State

#: The two attributes that carry the forecast, in the order they are read.
RAW_ATTRIBUTES: Final = ("raw_today", "raw_tomorrow")

#: The sensor's `price_type` attribute → the energy unit it means (D1 §5.2).
PRICE_TYPES: Final[dict[str, EnergyUnit]] = {
    "kwh": EnergyUnit.KWH,
    "mwh": EnergyUnit.MWH,
}


@register
@dataclass(frozen=True, slots=True)
class NordpoolHacs:
    """Reads the HACS Nord Pool sensor's raw attribute lists (D1 §2)."""

    key: ClassVar[str] = "nordpool_hacs"
    platform: ClassVar[str | None] = "nordpool"
    schema: ClassVar[Schema] = ()
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES

    def parse(self, state: State) -> ParsedPrices:
        """Return every priced interval the sensor knows about."""
        currency = state.attributes.get("currency")
        if not isinstance(currency, str) or not currency:
            raise SourceParseError(
                f"{state.entity_id} has no `currency` attribute; "
                "this does not look like a HACS Nord Pool sensor"
            )

        raw_unit = state.attributes.get("unit")
        energy = PRICE_TYPES.get(raw_unit.lower()) if isinstance(raw_unit, str) else None
        if energy is None:
            raise SourceParseError(
                f"{state.entity_id} prices per {raw_unit!r}; powerplan reads kWh and MWh"
            )

        # An absent list is simply nothing known yet - tomorrow, before 13:00.
        intervals = attribute_intervals(
            state,
            RAW_ATTRIBUTES,
            start_key="start",
            end_key="end",
            value_key="value",
            required=False,
        )

        return ParsedPrices(
            intervals=tuple(intervals),
            currency=currency,
            energy=energy,
            magnitude=Magnitude.MAJOR,
        )


__all__ = ["NordpoolHacs"]
