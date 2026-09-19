"""Tibber: `tibber.get_prices` (D1 §2, an action row).

The Tibber integration publishes the current price on a sensor and the rest of
the curve nowhere - `homeassistant/components/tibber/services.py` registers
`get_prices` as `SupportsResponse.ONLY`, takes optional `start` and `end` ISO
strings, and answers `{"prices": {<home nickname>: [{"start_time", "price",
"level"}]}}`. `price` is `price_total`: the home's own currency per kWh with VAT
and the retailer's markup already in it.

Two things this row refuses to guess. The response carries no currency, so the
currency is configuration - a Tibber account can be Norwegian, Swedish, German or
Dutch, and reading the wrong one into the site's money is worse than asking. And
one account can hold two homes: with two in the response and no `home`
configured, the adapter fails rather than picking the first, because the first is
as likely to be the cabin as the house.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.pricing import Field, FieldKind, Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices.action import async_response_action
from custom_components.powerplan.providers.prices.base import SourceParseError, local_day_bounds
from custom_components.powerplan.providers.prices.markets import TIBBER_MARKET

from .base import EntityFacts, FormatKind, ParsedPrices, interval_rows
from .registry import register

if TYPE_CHECKING:
    from datetime import date, tzinfo

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.pricing import Publication

#: The model core Tibber gives each home's price device (`sensor.py`,
#: `TibberSensorElPrice`); its Pulse devices are the other model in an entry.
PRICE_SENSOR_MODEL: Final = "Price Sensor"

#: The integration and action this row reads through.
TIBBER_DOMAIN: Final = "tibber"
SERVICE_GET_PRICES: Final = "get_prices"


@register
@dataclass(frozen=True, slots=True)
class TibberAction:
    """Reads one Tibber home's prices through `tibber.get_prices` (D1 §2)."""

    key: ClassVar[str] = "tibber_action"
    platform: ClassVar[str | None] = "tibber"
    kind: ClassVar[FormatKind] = FormatKind.ACTION
    schema: ClassVar[Schema] = (
        Field(key="currency", kind=FieldKind.TEXT, required=True),
        Field(key="home", kind=FieldKind.TEXT, default=""),
        Field(key="publication_tz", kind=FieldKind.TEXT, default=None, advanced=True),
        Field(key="publication_time", kind=FieldKind.TIME, default=None, advanced=True),
    )

    currency: str
    home: str = ""
    publication_tz: str = ""
    publication_time: time | str | None = None

    @staticmethod
    def from_entity(facts: EntityFacts) -> dict[str, str]:
        """Name the home only when the account has two (D1 §6).

        `get_prices` keys its answer by `home.name`, which is also the name core
        Tibber gives that home's price device; with one home the answer has one
        key and nothing needs naming.
        """
        homes = [name for name, model in facts.entry_devices if model == PRICE_SENSOR_MODEL]
        if len(homes) > 1 and facts.device_name:
            return {"home": facts.device_name}
        return {}

    def publication(self) -> Publication:
        """Return the day-ahead clock Tibber's markets settle on (INV-6, D-0100)."""
        return TIBBER_MARKET.publication(tz=self.publication_tz, local_time=self.publication_time)

    def native_unit(self) -> tuple[str, EnergyUnit, Magnitude]:
        """Return the configured currency, per kWh, in major units."""
        return (self.currency, EnergyUnit.KWH, Magnitude.MAJOR)

    async def fetch(self, hass: HomeAssistant, day: date, *, tz: tzinfo) -> ParsedPrices:
        """Ask Tibber for the local day `day` and read the home's list."""
        start, end = local_day_bounds(day, tz)
        response = await async_response_action(
            hass,
            TIBBER_DOMAIN,
            SERVICE_GET_PRICES,
            {"start": start.isoformat(), "end": end.isoformat()},
        )

        homes = response.get("prices")
        if not isinstance(homes, dict):
            raise SourceParseError(
                f"{TIBBER_DOMAIN}.{SERVICE_GET_PRICES} answered with "
                f"{type(homes).__name__} where the homes should be"
            )
        rows = self._home(homes)

        return ParsedPrices(
            intervals=tuple(
                interval_rows(
                    rows,
                    where=f"{TIBBER_DOMAIN}.{SERVICE_GET_PRICES}[{self.home or 'the only home'}]",
                    start_key="start_time",
                    value_key="price",
                )
            ),
            currency=self.currency,
            energy=EnergyUnit.KWH,
            magnitude=Magnitude.MAJOR,
        )

    def _home(self, homes: dict[str, object]) -> object:
        """Return the configured home's rows, refusing to guess between two."""
        if self.home:
            if self.home not in homes:
                raise SourceParseError(
                    f"Tibber answered for {sorted(homes)}, not for {self.home!r}"
                )
            return homes[self.home]
        if len(homes) > 1:
            raise SourceParseError(
                f"this Tibber account has {len(homes)} homes ({sorted(homes)}); "
                "name the one this site buys for"
            )
        if not homes:
            return []
        return next(iter(homes.values()))


__all__ = ["TibberAction"]
