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
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, ClassVar, Final

from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.pricing import Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices.base import Interval, SourceParseError

from .base import ParsedPrices
from .registry import register

if TYPE_CHECKING:
    from typing import Any

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

        intervals: list[Interval] = []
        for attribute in RAW_ATTRIBUTES:
            intervals.extend(_intervals(state, state.attributes.get(attribute), attribute))

        return ParsedPrices(
            intervals=tuple(intervals),
            currency=currency,
            energy=energy,
            magnitude=Magnitude.MAJOR,
        )


def _intervals(state: State, rows: Any, attribute: str) -> list[Interval]:
    """Read one `raw_*` list; an absent or empty list is simply nothing known."""
    if rows is None:
        return []
    if not isinstance(rows, list | tuple):
        raise SourceParseError(f"{state.entity_id}.{attribute} is not a list of slots")

    out: list[Interval] = []
    for row in rows:
        if not isinstance(row, dict):
            raise SourceParseError(f"{state.entity_id}.{attribute} holds {type(row).__name__}")
        value = row.get("value")
        if value is None:
            # The sensor could not price that hour - tomorrow before publication,
            # or an infinity it refused. A hole, for the forecaster to fill.
            continue
        out.append(
            Interval(
                start=_moment(state, row.get("start"), attribute),
                end=_moment(state, row.get("end"), attribute),
                value=_decimal(state, value, attribute),
            )
        )
    return out


def _moment(state: State, raw: Any, attribute: str) -> datetime:
    """Parse one boundary; the sensor stores `datetime`s, a dump stores ISO text."""
    if isinstance(raw, datetime):
        return raw
    parsed = dt_util.parse_datetime(raw) if isinstance(raw, str) else None
    if parsed is None:
        raise SourceParseError(f"{state.entity_id}.{attribute} has {raw!r} as a slot boundary")
    return parsed


def _decimal(state: State, raw: Any, attribute: str) -> Decimal:
    """Convert one price to `Decimal` through `str`, never through binary float."""
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as err:
        raise SourceParseError(f"{state.entity_id}.{attribute} has {raw!r} as a price") from err


__all__ = ["NordpoolHacs"]
