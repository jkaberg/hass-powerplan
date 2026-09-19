"""Tibber Prices (HACS): `tibber_prices.get_price` (D1 §2, an action row).

`jpawlowski/hass.tibber_prices` keeps its interval series behind a response
action: `services/get_price.py` registers `get_price` as `SupportsResponse.ONLY`,
takes `entry_id`, `start_time` and `end_time`, and answers
`{"success", "home_id", "price_info": [{"startsAt", "total", "energy", "tax",
"level"}], …}` - the Tibber API's own intervals (`api/client.py`'s query),
`total` in the home's currency per kWh. One config entry is one home, so the
entry picks the home and nothing is asked for it.

`success: false` is the integration saying the API was down for a range it had
not cached: that is a source that could not be reached, not an empty market.
The response carries no currency, so it is configuration, derived in the flow
from the picked sensor's unit - as for core Tibber's `tibber_action`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.pricing import Field, FieldKind, Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices.action import async_response_action
from custom_components.powerplan.providers.prices.base import (
    SourceUnavailableError,
    local_day_bounds,
)
from custom_components.powerplan.providers.prices.markets import TIBBER_MARKET

from .base import FormatKind, ParsedPrices, interval_rows
from .registry import register

if TYPE_CHECKING:
    from datetime import date, tzinfo

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.pricing import Publication

#: The integration and action this row reads through.
TIBBER_PRICES_DOMAIN: Final = "tibber_prices"
SERVICE_GET_PRICE: Final = "get_price"


@register
@dataclass(frozen=True, slots=True)
class TibberPrices:
    """Reads one Tibber Prices home through `tibber_prices.get_price` (D1 §2)."""

    key: ClassVar[str] = "tibber_prices"
    platform: ClassVar[str | None] = "tibber_prices"
    kind: ClassVar[FormatKind] = FormatKind.ACTION
    schema: ClassVar[Schema] = (
        Field(key="config_entry", kind=FieldKind.TEXT, required=True),
        Field(key="currency", kind=FieldKind.TEXT, required=True),
        Field(key="publication_tz", kind=FieldKind.TEXT, default=None, advanced=True),
        Field(key="publication_time", kind=FieldKind.TIME, default=None, advanced=True),
    )

    config_entry: str
    currency: str
    publication_tz: str = ""
    publication_time: time | str | None = None

    def publication(self) -> Publication:
        """Return the day-ahead clock Tibber's markets settle on (INV-6, D-0100)."""
        return TIBBER_MARKET.publication(tz=self.publication_tz, local_time=self.publication_time)

    def native_unit(self) -> tuple[str, EnergyUnit, Magnitude]:
        """Return the configured currency, per kWh, in major units."""
        return (self.currency, EnergyUnit.KWH, Magnitude.MAJOR)

    async def fetch(self, hass: HomeAssistant, day: date, *, tz: tzinfo) -> ParsedPrices:
        """Ask the entry's home for the local day `day`."""
        start, end = local_day_bounds(day, tz)
        response = await async_response_action(
            hass,
            TIBBER_PRICES_DOMAIN,
            SERVICE_GET_PRICE,
            {
                "entry_id": self.config_entry,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
            },
        )
        if response.get("success") is False:
            raise SourceUnavailableError(
                f"{TIBBER_PRICES_DOMAIN}.{SERVICE_GET_PRICE}: {response.get('reason')}"
            )
        return ParsedPrices(
            intervals=tuple(
                interval_rows(
                    response.get("price_info"),
                    where=f"{TIBBER_PRICES_DOMAIN}.{SERVICE_GET_PRICE}['price_info']",
                    start_key="startsAt",
                    value_key="total",
                )
            ),
            currency=self.currency,
            energy=EnergyUnit.KWH,
            magnitude=Magnitude.MAJOR,
        )


__all__ = ["TibberPrices"]
