"""Zonneplan: the tariff sensor's `forecast` (D1 §2).

`fsaris/home-assistant-zonneplan-one` puts the price series of its tariff
sensors on an attribute labelled `forecast` (`const.py`). Two shapes, both from
`coordinators/electricity_prices_data_coordinator.py`:

- the hourly and quarter-hourly sensors hand over the API's own series,
  `{start_date, end_date, price_tax_included: {amount}, …}`;
- the older *current electricity tariff* sensor hands over
  `{start_date, datetime, electricity_price, …}` - a start and no end.

Every amount is in **1e-7 euro** per kWh: the integration's own sensors multiply
by `value_factor=0.0000001` before showing `€/kWh`, and this row does the same.
The price includes Zonneplan's markup, the Dutch energy tax and VAT (O5).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.pricing import Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices.base import Interval, SourceParseError

from .base import FormatKind, ParsedPrices, interval_rows, listed
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State

#: The attribute label every tariff sensor uses for its series.
FORECAST_ATTRIBUTE: Final = "forecast"
#: What one unit of `amount` is worth in euro (`value_factor`).
AMOUNT_EURO: Final = Decimal("0.0000001")
#: The series' price, in the API's shape and in the legacy one.
SERIES_VALUE: Final = "price_tax_included.amount"
LEGACY_VALUE: Final = "electricity_price"


@register
@dataclass(frozen=True, slots=True)
class ZonneplanOne:
    """Reads a Zonneplan tariff sensor's forecast (D1 §2)."""

    key: ClassVar[str] = "zonneplan_one"
    platform: ClassVar[str | None] = "zonneplan_one"
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = ()
    basis: ClassVar[frozenset[str]] = frozenset({"spot", "vat", "levies"})

    def parse(self, state: State) -> ParsedPrices:
        """Return the forecast's slots in euro per kWh."""
        rows = state.attributes.get(FORECAST_ATTRIBUTE)
        if rows is None:
            raise SourceParseError(
                f"{state.entity_id} has no {FORECAST_ATTRIBUTE!r}; it has {sorted(state.attributes)}"
            )
        where = f"{state.entity_id}.{FORECAST_ATTRIBUTE}"
        listing = listed(rows, where=where)
        legacy = bool(listing) and isinstance(listing[0], dict) and LEGACY_VALUE in listing[0]
        found = interval_rows(
            listing,
            where=where,
            start_key="start_date",
            end_key=None if legacy else "end_date",
            value_key=LEGACY_VALUE if legacy else SERIES_VALUE,
        )
        return ParsedPrices(
            intervals=tuple(
                Interval(start=row.start, end=row.end, value=row.value * AMOUNT_EURO)
                for row in found
            ),
            currency="EUR",
            energy=EnergyUnit.KWH,
            magnitude=Magnitude.MAJOR,
        )


__all__ = ["ZonneplanOne"]
