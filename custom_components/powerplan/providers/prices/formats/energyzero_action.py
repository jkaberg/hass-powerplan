"""EnergyZero and easyEnergy: `{"prices": [...]}` from an action (D1 §2, one row).

D1 §2 writes these two as a single row - `energyzero_action` /
`easyenergy_action` - because they are the same integration twice: the same
author, the same Dutch day-ahead market, the same `SupportsResponse.ONLY`
services taking `config_entry`, `incl_vat`, `start` and `end`, and the same
answer, `{"prices": [{"timestamp": iso, "price": float, …}]}` in euro per kWh.
Only the service name and a couple of optional fields differ, so the reading is
one class and the registry gets two entries - which is what a "row" naming two
keys means (D1 §6's flow detects `energyzero` and `easyenergy` separately).

EnergyZero returns `start` and `end` beside `timestamp`; easyEnergy returns
neither, so its slot length is the next start and nothing else (INV-7). Both are
read the same way: `end` when it is there, the next start when it is not.

D1 §2 describes the response as `{prices: {iso: price}}`, which is what the
services answered when the table was written; they have published a list of
dicts since. §2 is amended with the shape the code parses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import TYPE_CHECKING, Any, ClassVar, Final

from custom_components.powerplan.core.pricing import Field, FieldKind, Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices.action import async_response_action
from custom_components.powerplan.providers.prices.base import SourceParseError, local_day_bounds
from custom_components.powerplan.providers.prices.markets import DUTCH_MARKET

from .base import FormatKind, ParsedPrices, interval_rows
from .registry import register

if TYPE_CHECKING:
    from datetime import date, tzinfo

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.pricing import Publication

#: Both integrations serve the Dutch market and quote in euro per kWh.
CURRENCY: Final = "EUR"

#: The Advanced override every source with a `Publication` renders (D-0100).
PUBLICATION_FIELDS: Final = (
    Field(key="publication_tz", kind=FieldKind.TEXT, default="", advanced=True),
    Field(key="publication_time", kind=FieldKind.TIME, default="", advanced=True),
)


@dataclass(frozen=True, slots=True)
class DutchPricesAction:
    """One Dutch day-ahead action: `{"prices": [{timestamp, price}]}` (D1 §2)."""

    #: The integration and service the subclass reads through.
    domain: ClassVar[str]
    service: ClassVar[str]

    config_entry: str
    incl_vat: bool = True
    publication_tz: str = ""
    publication_time: time | str | None = None

    def publication(self) -> Publication:
        """Return the Dutch day-ahead clock (INV-6, D-0100)."""
        return DUTCH_MARKET.publication(tz=self.publication_tz, local_time=self.publication_time)

    def native_unit(self) -> tuple[str, EnergyUnit, Magnitude]:
        """Return euro per kWh, in major units."""
        return (CURRENCY, EnergyUnit.KWH, Magnitude.MAJOR)

    def request(self, start: str, end: str) -> dict[str, Any]:
        """Return the action's data; a subclass adds what only it takes."""
        return {
            "config_entry": self.config_entry,
            "incl_vat": self.incl_vat,
            "start": start,
            "end": end,
        }

    async def fetch(self, hass: HomeAssistant, day: date, *, tz: tzinfo) -> ParsedPrices:
        """Ask the action for the local day `day` and read its price list."""
        start, end = local_day_bounds(day, tz)
        response = await async_response_action(
            hass, self.domain, self.service, self.request(start.isoformat(), end.isoformat())
        )

        rows = response.get("prices")
        if rows is None:
            raise SourceParseError(
                f"{self.domain}.{self.service} answered with {sorted(response)} and no `prices`"
            )

        return ParsedPrices(
            intervals=tuple(
                interval_rows(
                    rows,
                    where=f"{self.domain}.{self.service}['prices']",
                    start_key="timestamp",
                    end_key="end",
                    value_key="price",
                )
            ),
            currency=CURRENCY,
            energy=EnergyUnit.KWH,
            magnitude=Magnitude.MAJOR,
        )


@register
@dataclass(frozen=True, slots=True)
class EnergyZeroAction(DutchPricesAction):
    """EnergyZero's `get_energy_prices` (D1 §2)."""

    key: ClassVar[str] = "energyzero_action"
    platform: ClassVar[str | None] = "energyzero"
    kind: ClassVar[FormatKind] = FormatKind.ACTION
    domain: ClassVar[str] = "energyzero"
    service: ClassVar[str] = "get_energy_prices"
    schema: ClassVar[Schema] = (
        Field(key="config_entry", kind=FieldKind.TEXT, required=True),
        Field(key="incl_vat", kind=FieldKind.BOOL, default=True, required=True),
        Field(
            key="interval",
            kind=FieldKind.SELECT,
            default="hour",
            options=("hour", "quarter"),
            advanced=True,
        ),
        *PUBLICATION_FIELDS,
    )

    interval: str = "hour"

    def request(self, start: str, end: str) -> dict[str, Any]:
        """Add the MTU the Dutch market has traded in since October 2025."""
        return {**super().request(start, end), "interval": self.interval}


@register
@dataclass(frozen=True, slots=True)
class EasyEnergyAction(DutchPricesAction):
    """easyEnergy's `get_energy_usage_prices` (D1 §2)."""

    key: ClassVar[str] = "easyenergy_action"
    platform: ClassVar[str | None] = "easyenergy"
    kind: ClassVar[FormatKind] = FormatKind.ACTION
    domain: ClassVar[str] = "easyenergy"
    service: ClassVar[str] = "get_energy_usage_prices"
    schema: ClassVar[Schema] = (
        Field(key="config_entry", kind=FieldKind.TEXT, required=True),
        Field(key="incl_vat", kind=FieldKind.BOOL, default=True, required=True),
        Field(
            key="granularity",
            kind=FieldKind.SELECT,
            default="hour",
            options=("hour", "4hour", "day"),
            advanced=True,
        ),
        *PUBLICATION_FIELDS,
    )

    granularity: str = "hour"

    def request(self, start: str, end: str) -> dict[str, Any]:
        """Add easyEnergy's own name for the resolution it answers in."""
        return {**super().request(start, end), "granularity": self.granularity}


__all__ = ["DutchPricesAction", "EasyEnergyAction", "EnergyZeroAction"]
